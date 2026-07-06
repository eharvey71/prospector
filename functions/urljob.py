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
from typing import Optional

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
    if len(text) < 200:
        raise ValueError(
            "that page has almost no readable text (likely rendered by "
            "JavaScript) — the engine can't read it; apply manually or "
            "paste a different URL for the same job")

    facts = generate_structured(
        f"Job posting page text:\n\n{text[:8000]}",
        PostingFacts,
        system=EXTRACT_SYSTEM,
        max_tokens=300,
    )
    company = (facts.company or httpx.URL(url).host or "unknown").strip()

    posting = JobPosting(
        source=_infer_source(url),
        external_id=url,
        company=company,
        title=(facts.title or "").strip() or "(title not found)",
        url=url,
        location=facts.location,
        description_text=text,
    )
    doc = posting.model_dump(mode="json")
    db.collection("jobPostings").document(posting.posting_id).set(doc, merge=True)
    log.info("user-added posting %s: %s @ %s", posting.posting_id,
             posting.title, company)
    return posting.posting_id, doc
