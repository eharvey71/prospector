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
from typing import Optional
from urllib.parse import urljoin

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
                             headers={"User-Agent": "job-engine/0.1"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("iframe %s failed: %s", src, exc)
            continue
        frame_text = _strip_html(resp.text)[:20_000]
        if len(frame_text) > len(text):
            text = frame_text
    return text


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
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "job-engine/0.1"})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"could not fetch that URL: {exc}") from exc

    text = _strip_html(resp.text)[:20_000]
    if len(text) < 1500:  # thin page -> the real posting may be in an iframe
        text = _follow_iframes(str(resp.url), resp.text, text)
    if len(text) < 200:
        raise ValueError(
            "that page has almost no readable text (likely rendered by "
            "JavaScript) — the engine can't read it; apply manually or "
            "paste a different URL for the same job")

    page_title = _page_title(resp.text)
    facts = generate_structured(
        (f"Page <title> tag: {page_title}\n\n" if page_title else "")
        + f"Job posting page text:\n\n{text[:8000]}",
        PostingFacts,
        system=EXTRACT_SYSTEM,
        max_tokens=300,
    )
    company = (facts.company or httpx.URL(url).host or "unknown").strip()

    posting = JobPosting(
        source=_infer_source(url),
        external_id=url,
        company=company,
        title=(facts.title or "").strip() or page_title or "(title not found)",
        url=url,
        location=facts.location,
        description_text=text,
    )
    doc = posting.model_dump(mode="json")
    db.collection("jobPostings").document(posting.posting_id).set(doc, merge=True)
    log.info("user-added posting %s: %s @ %s", posting.posting_id,
             posting.title, company)
    return posting.posting_id, doc
