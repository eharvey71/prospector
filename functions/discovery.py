"""Job discovery from public ATS JSON endpoints.

No browsers, no scraping heroics — Greenhouse and Lever expose clean JSON per
company board. The crawl reads each user's watchlist, unions the company
lists, fetches boards, and upserts postings keyed by a deterministic hash so
re-runs are natural dedup.

Watchlist doc shape (users/{uid}/watchlist/companies):
    { "greenhouse": ["anthropic", "stripe"], "lever": ["plaid"],
      "custom": ["https://acmerobotics.com/careers"],
      "workday": ["https://pearson.wd3.myworkdayjobs.com/en-US/Pearson_Careers"] }

Workday: read via the public CxS JSON API (no login needed to browse), so
big enterprise boards flow through discovery + drafting. Submission stays
manual — source=workday has no adapter, so the worker escalates a prepared
application to needs_human.

Custom career pages: the page's links are collected, an LLM picks which
ones are individual job postings, and only NEW links (no posting with that
url yet) are fetched and ingested — so the recurring LLM cost is one link-
classification per page per crawl. JS-rendered pages yield no links and
are skipped with a log line.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urldefrag, urljoin

import httpx
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel, Field

from schemas import AtsType, JobPosting

log = logging.getLogger("discovery")

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{board}?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
SMARTRECRUITERS_URL = "https://api.smartrecruiters.com/v1/companies/{board}/postings"
SMARTRECRUITERS_DETAIL = "https://api.smartrecruiters.com/v1/companies/{board}/postings/{pid}"
WORKABLE_URL = "https://apply.workable.com/api/v1/widget/accounts/{board}?details=true"
MAX_DETAIL_FETCHES = 80       # per-board cap on per-job description requests

HTTP_TIMEOUT = 20.0
MAX_LINKS_TO_CLASSIFY = 150   # anchors handed to the LLM per career page
MAX_NEW_PER_PAGE = 15         # new postings ingested per page per crawl
WORKDAY_PAGE = 20             # CxS page size (server max)
MAX_WORKDAY_JOBS = 80         # cap per Workday board per crawl

LINK_SYSTEM = """You are given links found on a company's careers page. \
Return the hrefs that point to INDIVIDUAL job postings — not category \
pages, login pages, blog posts, or the careers page itself. When unsure, \
leave it out."""


class JobLinkPick(BaseModel):
    hrefs: list[str] = Field(default_factory=list)


def run_discovery(db: firestore.Client) -> int:
    """Full crawl. Returns number of postings upserted.

    Every source is isolated: one broken board, dead career page, or LLM
    outage (career-page link classification is the only LLM dependency
    here) must never take down the rest of the crawl."""
    boards = _collect_watchlists(db)
    count = 0

    def safe(fn, *args, label: str) -> int:
        try:
            return fn(*args)
        except Exception:
            log.exception("discovery source %s failed; continuing", label)
            return 0

    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": "job-engine/0.1"}) as client:
        for board in sorted(boards.get("greenhouse", set())):
            count += safe(lambda b=board: _upsert_all(db, _fetch_greenhouse(client, b)),
                          label=f"greenhouse:{board}")
        for board in sorted(boards.get("lever", set())):
            count += safe(lambda b=board: _upsert_all(db, _fetch_lever(client, b)),
                          label=f"lever:{board}")
        for wd_url in sorted(boards.get("workday", set())):
            count += safe(lambda u=wd_url: _upsert_all(db, _fetch_workday(client, u)),
                          label=f"workday:{wd_url}")
        for board in sorted(boards.get("ashby", set())):
            count += safe(lambda b=board: _upsert_all(db, _fetch_ashby(client, b)),
                          label=f"ashby:{board}")
        for board in sorted(boards.get("smartrecruiters", set())):
            count += safe(lambda b=board: _upsert_all(db, _fetch_smartrecruiters(client, b)),
                          label=f"smartrecruiters:{board}")
        for board in sorted(boards.get("workable", set())):
            count += safe(lambda b=board: _upsert_all(db, _fetch_workable(client, b)),
                          label=f"workable:{board}")
        # Career pages last — they're the only LLM-dependent source, so an
        # API outage degrades to "no career-page postings this crawl".
        for page_url in sorted(boards.get("custom", set())):
            count += safe(lambda p=page_url: _crawl_career_page(db, client, p),
                          label=f"custom:{page_url}")
    log.info("discovery complete: %d postings upserted", count)
    return count


WATCHLIST_FIELDS = ("greenhouse", "lever", "custom", "workday",
                    "ashby", "smartrecruiters", "workable")


def _collect_watchlists(db: firestore.Client) -> dict[str, set[str]]:
    boards: dict[str, set[str]] = {f: set() for f in WATCHLIST_FIELDS}
    for doc in db.collection_group("watchlist").stream():
        data = doc.to_dict() or {}
        for f in WATCHLIST_FIELDS:
            boards[f].update(data.get(f, []))
    return boards


# ---------------------------------------------------------------------------
# Custom career pages
# ---------------------------------------------------------------------------

def _crawl_career_page(db: firestore.Client, client: httpx.Client,
                       page_url: str) -> int:
    try:
        resp = client.get(page_url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("career page %s failed: %s", page_url, exc)
        return 0

    links = _extract_links(resp.text, str(resp.url))
    if not links:
        log.warning("career page %s: no links found (JS-rendered?)", page_url)
        return 0

    from llm import generate_structured
    listing = "\n".join(f"{text[:80]} -> {href}"
                        for href, text in links[:MAX_LINKS_TO_CLASSIFY])
    pick = generate_structured(
        f"Links from {page_url}:\n\n{listing}",
        JobLinkPick,
        system=LINK_SYSTEM,
        max_tokens=2000,
    )
    valid = {href for href, _ in links}
    job_links = [h for h in pick.hrefs if h in valid]

    from urljob import create_posting_from_url
    count = 0
    for href in job_links:
        if count >= MAX_NEW_PER_PAGE:
            break
        exists = (
            db.collection("jobPostings")
            .where(filter=FieldFilter("url", "==", href)).limit(1).get()
        )
        if exists:
            continue
        try:
            create_posting_from_url(db, href)
            count += 1
        except Exception as exc:
            log.warning("career page link %s failed: %s", href, exc)
    log.info("career page %s: %d new postings", page_url, count)
    return count


def _extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """(absolute_href, link_text) for every anchor, deduped, fragments
    stripped."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
            r"<a\s[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>",
            html, re.I | re.S):
        href = urldefrag(urljoin(base_url, m.group(1))).url
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        out.append((href, text))
    return out


def _fetch_greenhouse(client: httpx.Client, board: str) -> list[JobPosting]:
    try:
        resp = client.get(GREENHOUSE_URL.format(board=board))
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("greenhouse board %s failed: %s", board, exc)
        return []
    postings = []
    for job in resp.json().get("jobs", []):
        postings.append(JobPosting(
            source=AtsType.GREENHOUSE,
            external_id=str(job["id"]),
            company=board,
            title=job.get("title", ""),
            url=job.get("absolute_url"),
            location=(job.get("location") or {}).get("name"),
            description_text=_strip_html(job.get("content", "")),
        ))
    return postings


def _fetch_lever(client: httpx.Client, board: str) -> list[JobPosting]:
    try:
        resp = client.get(LEVER_URL.format(board=board))
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("lever board %s failed: %s", board, exc)
        return []
    postings = []
    for job in resp.json():
        postings.append(JobPosting(
            source=AtsType.LEVER,
            external_id=job["id"],
            company=board,
            title=job.get("text", ""),
            url=job.get("hostedUrl"),
            location=(job.get("categories") or {}).get("location"),
            description_text=_strip_html(job.get("descriptionPlain") or job.get("description", "")),
        ))
    return postings


# ---------------------------------------------------------------------------
# Ashby / SmartRecruiters / Workable (public JSON APIs, no auth)
# ---------------------------------------------------------------------------

def _fetch_ashby(client: httpx.Client, board: str) -> list[JobPosting]:
    try:
        resp = client.get(ASHBY_URL.format(board=board))
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("ashby board %s failed: %s", board, exc)
        return []
    postings = []
    for job in jobs:
        url = job.get("jobUrl") or job.get("applyUrl")
        if not job.get("id") or not url:
            continue
        postings.append(JobPosting(
            source=AtsType.ASHBY,
            external_id=str(job["id"]),
            company=board,
            title=job.get("title", ""),
            url=url,
            location=job.get("location"),
            description_text=_strip_html(job.get("descriptionHtml", "")),
        ))
    return postings


def _fetch_smartrecruiters(client: httpx.Client, board: str) -> list[JobPosting]:
    try:
        resp = client.get(SMARTRECRUITERS_URL.format(board=board))
        resp.raise_for_status()
        items = resp.json().get("content", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("smartrecruiters board %s failed: %s", board, exc)
        return []
    postings = []
    for item in items[:MAX_DETAIL_FETCHES]:
        pid = item.get("id")
        if not pid:
            continue
        # Listing has no description; the detail endpoint has jobAd sections.
        description = ""
        try:
            detail = client.get(SMARTRECRUITERS_DETAIL.format(board=board, pid=pid))
            detail.raise_for_status()
            sections = (detail.json().get("jobAd") or {}).get("sections") or {}
            description = _strip_html(" ".join(
                (s or {}).get("text", "") for s in sections.values()))
        except (httpx.HTTPError, ValueError):
            pass
        postings.append(JobPosting(
            source=AtsType.SMARTRECRUITERS,
            external_id=str(pid),
            company=board,
            title=item.get("name", ""),
            url=f"https://jobs.smartrecruiters.com/{board}/{pid}",
            location=((item.get("location") or {}).get("city")),
            description_text=description,
        ))
    return postings


def _fetch_workable(client: httpx.Client, board: str) -> list[JobPosting]:
    try:
        resp = client.get(WORKABLE_URL.format(board=board))
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("workable board %s failed: %s", board, exc)
        return []
    postings = []
    for job in jobs:
        code = job.get("shortcode")
        url = job.get("url") or (f"https://apply.workable.com/{board}/j/{code}/"
                                 if code else None)
        if not code or not url:
            continue
        postings.append(JobPosting(
            source=AtsType.WORKABLE,
            external_id=str(code),
            company=board,
            title=job.get("title", ""),
            url=url,
            location=(job.get("location") or {}).get("city")
                     or job.get("country"),
            description_text=_strip_html(job.get("description", "")),
        ))
    return postings


# ---------------------------------------------------------------------------
# Workday (public CxS JSON API)
# ---------------------------------------------------------------------------

def _parse_workday_url(url: str) -> tuple[str, str, str] | None:
    """A myworkdayjobs careers URL -> (host, tenant, site).

    https://pearson.wd3.myworkdayjobs.com/en-US/Pearson_Careers
        -> ("pearson.wd3.myworkdayjobs.com", "pearson", "Pearson_Careers")
    The optional locale segment (en-US / en_US) is skipped.
    """
    m = re.match(r"https?://(([a-z0-9-]+)\.[a-z0-9-]+\.myworkdayjobs\.com)(/.*)?",
                 url.strip(), re.I)
    if not m:
        return None
    host, tenant = m.group(1), m.group(2)
    segments = [s for s in (m.group(3) or "").split("/") if s]
    if segments and re.fullmatch(r"[a-z]{2}[-_][A-Za-z]{2}", segments[0]):
        segments = segments[1:]
    if not segments:
        return None
    return host, tenant, segments[0]


def _fetch_workday(client: httpx.Client, url: str) -> list[JobPosting]:
    parsed = _parse_workday_url(url)
    if not parsed:
        log.warning("workday url %s not recognized", url)
        return []
    host, tenant, site = parsed
    cxs = f"https://{host}/wday/cxs/{tenant}/{site}"

    postings: list[JobPosting] = []
    offset = 0
    while offset < MAX_WORKDAY_JOBS:
        try:
            resp = client.post(f"{cxs}/jobs", json={
                "appliedFacets": {}, "limit": WORKDAY_PAGE,
                "offset": offset, "searchText": "",
            })
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("workday %s page %d failed: %s", tenant, offset, exc)
            break
        batch = data.get("jobPostings", [])
        if not batch:
            break
        for job in batch:
            ext_path = job.get("externalPath", "")
            if not ext_path:
                continue
            postings.append(JobPosting(
                source=AtsType.WORKDAY,
                external_id=ext_path,
                company=tenant,
                title=job.get("title", ""),
                url=f"https://{host}/{site}{ext_path}",
                location=job.get("locationsText"),
                description_text=_workday_description(client, cxs, ext_path),
            ))
        offset += WORKDAY_PAGE
        if offset >= data.get("total", 0):
            break
    log.info("workday %s: %d postings", tenant, len(postings))
    return postings


def _workday_description(client: httpx.Client, cxs: str, ext_path: str) -> str:
    try:
        resp = client.get(f"{cxs}{ext_path}")
        resp.raise_for_status()
        info = resp.json().get("jobPostingInfo", {})
        return _strip_html(info.get("jobDescription", ""))
    except (httpx.HTTPError, ValueError):
        return ""


def _upsert_all(db: firestore.Client, postings: list[JobPosting]) -> int:
    now = datetime.now(timezone.utc)
    batch = db.batch()
    for i, p in enumerate(postings):
        ref = db.collection("jobPostings").document(p.posting_id)
        doc = p.model_dump(mode="json")
        doc["lastSeen"] = now
        doc["active"] = True
        # merge=True keeps firstSeen from the original write
        batch.set(ref, doc, merge=True)
        if (i + 1) % 400 == 0:  # Firestore batch limit is 500 ops
            batch.commit()
            batch = db.batch()
    batch.commit()
    return len(postings)


def _strip_html(html: str) -> str:
    import re
    from html import unescape
    text = re.sub(r"<[^>]+>", " ", unescape(html))
    return re.sub(r"\s+", " ", text).strip()
