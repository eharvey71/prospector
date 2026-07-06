"""Job discovery from public ATS JSON endpoints.

No browsers, no scraping heroics — Greenhouse and Lever expose clean JSON per
company board. The crawl reads each user's watchlist, unions the company
lists, fetches boards, and upserts postings keyed by a deterministic hash so
re-runs are natural dedup.

Watchlist doc shape (users/{uid}/watchlist/companies):
    { "greenhouse": ["anthropic", "stripe"], "lever": ["plaid"],
      "custom": ["https://acmerobotics.com/careers"] }

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

HTTP_TIMEOUT = 20.0
MAX_LINKS_TO_CLASSIFY = 150   # anchors handed to the LLM per career page
MAX_NEW_PER_PAGE = 15         # new postings ingested per page per crawl

LINK_SYSTEM = """You are given links found on a company's careers page. \
Return the hrefs that point to INDIVIDUAL job postings — not category \
pages, login pages, blog posts, or the careers page itself. When unsure, \
leave it out."""


class JobLinkPick(BaseModel):
    hrefs: list[str] = Field(default_factory=list)


def run_discovery(db: firestore.Client) -> int:
    """Full crawl. Returns number of postings upserted."""
    boards = _collect_watchlists(db)
    count = 0
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": "job-engine/0.1"}) as client:
        for board in sorted(boards.get("greenhouse", set())):
            count += _upsert_all(db, _fetch_greenhouse(client, board))
        for board in sorted(boards.get("lever", set())):
            count += _upsert_all(db, _fetch_lever(client, board))
        for page_url in sorted(boards.get("custom", set())):
            count += _crawl_career_page(db, client, page_url)
    log.info("discovery complete: %d postings upserted", count)
    return count


def _collect_watchlists(db: firestore.Client) -> dict[str, set[str]]:
    boards: dict[str, set[str]] = {"greenhouse": set(), "lever": set(), "custom": set()}
    for doc in db.collection_group("watchlist").stream():
        data = doc.to_dict() or {}
        boards["greenhouse"].update(data.get("greenhouse", []))
        boards["lever"].update(data.get("lever", []))
        boards["custom"].update(data.get("custom", []))
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
