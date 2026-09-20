"""User-added jobs: arbitrary posting URL -> jobPosting -> pipeline.

The front door for ATSes the crawler doesn't know. The page is fetched and
stripped to text, an LLM extracts company/title/location (facts only — the
description is the page text itself, not LLM output), and the posting enters
Firestore with source=unknown so submission routes to the Tier 2 agentic
adapter. Because the user chose this job deliberately, matching runs with
the score gate bypassed: it always proceeds to drafting, score recorded.
"""
from __future__ import annotations

import logging
import re
import time
from html import unescape
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from pydantic import BaseModel

from discovery import _strip_html
from llm import generate_structured
from schemas import AtsType, JobPosting

log = logging.getLogger("urljob")

HTTP_TIMEOUT = 20.0

EXTRACT_SYSTEM = """You extract basic facts from the text of a job posting \
page. Only report what's stated; use null when a field isn't clear."""


class PostingFacts(BaseModel):
    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None


IFRAME_SRC = re.compile(r"<iframe[^>]+src=[\"']([^\"']+)[\"']", re.I)


def _page_title(html: str) -> Optional[str]:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not m:
        return None
    title = _strip_html(m.group(1)).strip()
    return title[:120] or None


def _follow_iframes(client_url: str, html: str, text: str) -> str:
    """Many career pages are just a shell around an ATS iframe (iCIMS,
    embedded Greenhouse, …) — the visible text lives in the frame. When the
    outer page is thin, fetch the frames and keep the longest text found."""
    for src in IFRAME_SRC.findall(html)[:3]:
        src = urljoin(client_url, src)
        if not src.startswith("http"):
            continue
        try:
            resp = httpx.get(src, timeout=HTTP_TIMEOUT, follow_redirects=True,
                             headers=BROWSER_HEADERS)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("iframe %s failed: %s", src, exc)
            continue
        frame_text = _strip_html(resp.text)[:20_000]
        if len(frame_text) > len(text):
            text = frame_text
    return text


LINKEDIN_GUEST = ("https://www.linkedin.com/jobs-guest/jobs/api/"
                  "jobPosting/{job_id}")
# Browser UA: the guest endpoints 403 a bot-looking agent.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def linkedin_job_id(url: str) -> Optional[str]:
    """The numeric posting id from any LinkedIn job URL shape."""
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]currentJobId=(\d{6,})", url)
    return m.group(1) if m else None


def expand_short_link(url: str) -> str:
    """lnkd.in/abc123 -> the real posting URL. Shared LinkedIn links are
    almost always shortened, and the id parser can't see through them."""
    if "lnkd.in/" not in url:
        return url
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": BROWSER_UA})
        return str(resp.url)
    except httpx.HTTPError:
        return url


def resolve_linkedin(url: str) -> tuple[str, str]:
    """LinkedIn posting URL -> (best_url, description_text).

    LinkedIn has no public API, but the logged-out "guest" job endpoint
    serves the posting as plain HTML. Most listings carry an "Apply on
    company website" offsite link — that's the employer's real ATS page,
    which is where we'd rather send the pipeline (Tier 1 territory).
    Returns the original URL unchanged when nothing better is found.
    """
    job_id = linkedin_job_id(url)
    if not job_id:
        return url, ""
    # LinkedIn rate-limits cloud egress (Functions run on Google IPs) and
    # answers 429/999 in bursts, so a single attempt fails far more often
    # than the posting is actually unavailable. Three tries, backing off.
    resp = None
    for attempt in range(3):
        try:
            r = httpx.get(LINKEDIN_GUEST.format(job_id=job_id),
                          timeout=HTTP_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": BROWSER_UA,
                                   "Accept": "text/html,*/*;q=0.8",
                                   "Accept-Language": "en-US,en;q=0.9"})
            if r.status_code < 400:
                resp = r
                break
            log.info("linkedin guest %s attempt %d: HTTP %s",
                     job_id, attempt + 1, r.status_code)
        except httpx.HTTPError as exc:
            log.info("linkedin guest %s attempt %d: %s", job_id, attempt + 1, exc)
        time.sleep(1.5 * (attempt + 1))
    if resp is None:
        log.warning("linkedin guest fetch %s failed after retries", job_id)
        return url, ""

    html = resp.text
    text = _strip_html(html)[:20_000]

    # The offsite apply link is served through a LinkedIn redirect wrapper;
    # the real destination is the url= parameter.
    m = re.search(r'href="(https://www\.linkedin\.com/jobs/view/externalApply/[^"]+)"', html)
    if m:
        wrapper = unescape(m.group(1))
        target = parse_qs(urlparse(wrapper).query).get("url", [None])[0]
        if target and target.startswith("http"):
            log.info("linkedin %s -> external apply %s", job_id, target[:120])
            return target, text
    # Some listings embed the destination directly.
    m = re.search(r'"companyApplyUrl":"(https:[^"]+)"', html)
    if m:
        target = m.group(1).encode().decode("unicode_escape")
        log.info("linkedin %s -> companyApplyUrl %s", job_id, target[:120])
        return target, text
    log.info("linkedin %s: no offsite apply link (Easy Apply only)", job_id)
    return url, text


BAMBOO_RX = re.compile(r"https?://([\w-]+)\.bamboohr\.com/careers/(\d+)", re.I)


def resolve_bamboohr(url: str) -> Optional[tuple[str, str, Optional[str], str]]:
    """BambooHR careers pages are JS-rendered shells with no readable text;
    the posting itself is served from a public JSON endpoint. Returns
    (company, title, location, description_text), or None when the URL
    isn't BambooHR or the endpoint doesn't cooperate — the caller then
    falls back to the generic fetch and its honest error."""
    m = BAMBOO_RX.search(url)
    if not m:
        return None
    sub, jid = m.group(1), m.group(2)
    try:
        resp = httpx.get(
            f"https://{sub}.bamboohr.com/careers/{jid}/detail",
            timeout=HTTP_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("bamboohr detail fetch failed for %s: %s", url, exc)
        return None
    # Observed nestings: {result: {jobOpening: {...}}} / {result: {...}} /
    # flat. Walk down whatever is there.
    j = data.get("result") if isinstance(data.get("result"), dict) else data
    if isinstance(j, dict) and isinstance(j.get("jobOpening"), dict):
        j = j["jobOpening"]
    if not isinstance(j, dict):
        return None
    title = str(j.get("jobOpeningName") or j.get("title") or "").strip()
    desc = _strip_html(unescape(str(j.get("description") or "")))
    loc = j.get("location")
    if isinstance(loc, dict):
        location = ", ".join(
            x for x in (loc.get("city"), loc.get("state")) if x) or None
    else:
        location = (str(loc).strip() or None) if loc else None
    company = str(j.get("companyName") or sub).strip()
    if not title or len(desc) < 80:
        log.warning("bamboohr %s: unusable payload (title=%r, %d chars)",
                    url, title, len(desc))
        return None
    return company, title, location, desc[:20_000]


BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_page(url: str) -> httpx.Response:
    """Fetch a posting page the way a browser would.

    A bot-looking User-Agent is a common cause of 403/404 from ATS CDNs
    (Cloudflare/Akamai in front of Greenhouse, iCIMS, Workday and friends)
    — the posting is fine, the client just wasn't welcome. Browser headers
    first; on a 4xx, one retry with the site as referer, which a few
    boards require for direct job links."""
    resp = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True,
                     headers=BROWSER_HEADERS)
    if resp.status_code in (401, 403, 404, 405, 429):
        try:
            origin = str(httpx.URL(url).copy_with(path="/", query=None, fragment=None))
            retry = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True,
                              headers={**BROWSER_HEADERS, "Referer": origin})
            if retry.status_code < 400:
                return retry
        except httpx.HTTPError:
            pass
    resp.raise_for_status()
    return resp


def _fetch_hint(url: str, exc: Exception) -> str:
    """User-facing message that says which KIND of failure this was."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 404:
        return ("that posting URL returns 404 — the job was probably taken "
                "down, or the link is a search result rather than the "
                "posting itself; open it in your browser to check")
    if status in (401, 403):
        return ("that site refused an automated fetch (403) — open the "
                "posting in your browser and use the extension's toolbar "
                "button to add it instead")
    return f"could not fetch that URL: {exc}"


BOT_WALL_RX = re.compile(
    r"confirm you are (not a robot|a human)|are you a human"
    r"|verify (you are|you're) (human|not a robot)"
    r"|checking your browser|just a moment"
    r"|enable javascript( and cookies)? to continue"
    r"|access denied|request (blocked|unsuccessful)"
    r"|unusual traffic|\bcaptcha\b|cf-ray|cloudflare ray id", re.I)

# A wall LEADS with its message, so the opening of the page is the
# reliable signal — some sites wrap the wall in their full nav and footer,
# which defeats a page-length test. A posting that merely mentions
# "captcha" deep in its text still goes through.
BOT_WALL_HEAD_CHARS = 600
BOT_WALL_MAX_CHARS = 1200


def _is_bot_wall(text: str) -> bool:
    if BOT_WALL_RX.search(text[:BOT_WALL_HEAD_CHARS]):
        return True
    return len(text) < BOT_WALL_MAX_CHARS and bool(BOT_WALL_RX.search(text))


def is_wall_title(title: str) -> bool:
    """Last line of defence, applied to the EXTRACTED title. Whatever the
    page heuristics miss shows up here: a posting called "Please click to
    confirm you are not a robot" is never a real job."""
    return bool(BOT_WALL_RX.search(title or ""))


def _infer_source(url: str) -> AtsType:
    """Hosted Greenhouse/Lever postings get their Tier-1 adapter even when
    found via a career page or pasted by hand."""
    host = httpx.URL(url).host or ""
    if "greenhouse.io" in host:
        return AtsType.GREENHOUSE
    if "lever.co" in host:
        return AtsType.LEVER
    return AtsType.UNKNOWN


def create_posting_from_url(db, url: str) -> tuple[str, dict]:
    """Fetch, extract, upsert. Returns (posting_id, posting_dict).
    Raises ValueError with a user-facing message on fetch problems."""
    # LinkedIn (and other aggregators) are indexes, not application
    # destinations: follow through to the employer's own ATS page when the
    # listing offers one, so submission lands in Tier 1 instead of Tier 2.
    # Backstop for the extension's toolbar fallback: Prospector is not a
    # job posting, and "Prospector @ job-engine-c8f9c.web.app" in the
    # queue helps nobody.
    if re.search(r"(^|\.)job-engine-c8f9c\.web\.app", httpx.URL(url).host or ""):
        raise ValueError("that's Prospector itself, not a job posting")

    url = expand_short_link(url)
    linkedin_text = ""
    if "linkedin.com/jobs" in url:
        resolved, linkedin_text = resolve_linkedin(url)
        if resolved != url:
            url = resolved
        elif linkedin_text and len(linkedin_text) > 400:
            # Easy-Apply-only listing: keep LinkedIn's own description
            # rather than failing, and let the human apply there.
            return _upsert_from_text(db, url, linkedin_text)
        else:
            # Guest endpoint blocked or empty. Falling through to fetch
            # linkedin.com directly is pointless — LinkedIn serves a 404
            # to logged-out server clients, which then reads as "this job
            # was taken down" when the posting is perfectly alive.
            raise ValueError(
                "LinkedIn blocks automated reads from cloud servers, so the "
                "engine can't open this posting (the job itself is probably "
                "fine). Open it in your browser and use the 'Apply on "
                "company website' link, then paste THAT employer URL — the "
                "engine can read those, and can usually submit them "
                "automatically. LinkedIn Easy Apply jobs have to be done by "
                "hand on LinkedIn.")

    # BambooHR: the page is an empty JS shell, but the posting is public
    # JSON — no fetch-and-guess, no LLM extraction needed.
    bamboo = resolve_bamboohr(url)
    if bamboo:
        company, title, location, text = bamboo
        return _upsert(db, url, company=company, title=title,
                       location=location, text=text)

    try:
        resp = _fetch_page(url)
    except httpx.HTTPError as exc:
        raise ValueError(_fetch_hint(url, exc)) from exc

    text = _strip_html(resp.text)[:20_000]
    if len(text) < 1500:  # thin page -> the real posting may be in an iframe
        text = _follow_iframes(str(resp.url), resp.text, text)
    # A bot-check wall answers 200 with a page whose text is "confirm you
    # are not a robot". Left alone, that text becomes the posting: the
    # extraction LLM dutifully makes it the TITLE, matching scores the
    # empty job 5/10 across the board, and the queue shows a job named
    # "Please click to confirm you are not a robot".
    if _is_bot_wall(text):
        raise ValueError(
            "that site answered with a bot-check page instead of the "
            "posting, so there's nothing to read. Open it in your browser "
            "and paste the URL you land on after the check, or use a "
            "direct link to the employer's application form")
    if len(text) < 200:
        raise ValueError(
            "that page has almost no readable text (likely rendered by "
            "JavaScript) — the engine can't read it; apply manually or "
            "paste a different URL for the same job")

    return _upsert_from_text(db, url, text, page_title=_page_title(resp.text))


def _upsert(db, url: str, *, company: str, title: str,
            location: Optional[str], text: str) -> tuple[str, dict]:
    posting = JobPosting(
        source=_infer_source(url),
        external_id=url,
        company=company,
        title=title,
        url=url,
        location=location,
        description_text=text,
    )
    doc = posting.model_dump(mode="json")
    db.collection("jobPostings").document(posting.posting_id).set(doc, merge=True)
    log.info("user-added posting %s: %s @ %s", posting.posting_id, title, company)
    return posting.posting_id, doc


def _upsert_from_text(db, url: str, text: str,
                      page_title: Optional[str] = None) -> tuple[str, dict]:
    """Extract facts from posting text and upsert the posting."""
    facts = generate_structured(
        (f"Page <title> tag: {page_title}\n\n" if page_title else "")
        + f"Job posting page text:\n\n{text[:8000]}",
        PostingFacts,
        system=EXTRACT_SYSTEM,
        max_tokens=300,
        role="extract",
    )
    title = (facts.title or "").strip() or page_title or "(title not found)"
    # Backstop: if the extracted title is a bot-check message, the page was
    # a wall no matter what the text heuristics thought.
    if is_wall_title(title) or is_wall_title(page_title or ""):
        raise ValueError(
            "that site answered with a bot-check page instead of the "
            "posting, so there's nothing to read. Open it in your browser "
            "and paste the URL you land on after the check, or use a "
            "direct link to the employer's application form")
    return _upsert(
        db, url,
        company=(facts.company or httpx.URL(url).host or "unknown").strip(),
        title=title,
        location=facts.location,
        text=text,
    )
