"""Company suggester: role description -> verified Greenhouse/Lever boards.

The LLM proposes candidate companies for the role; nothing it says is
trusted directly. Every candidate slug is probed against the public board
APIs, and only boards that actually exist (with live postings) are
returned. The client adds chosen ones to the watchlist — suggestions never
write anything themselves.
"""
from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel, Field

from llm import generate_structured

log = logging.getLogger("suggest")

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

MAX_CANDIDATES = 20
HTTP_TIMEOUT = 6.0

SUGGEST_SYSTEM = """You suggest employers for a job seeker. Given a role \
description, list companies genuinely likely to hire for it. Prefer \
companies known to run public job boards on Greenhouse or Lever (common \
among startups and mid-size tech companies). For each company give the \
likely board slug: lowercase, no spaces (e.g. "Notion Labs" -> "notion"). \
Include a mix of well-known and less obvious employers."""


class CompanyCandidate(BaseModel):
    company: str
    slug: str


class CandidateList(BaseModel):
    candidates: list[CompanyCandidate] = Field(default_factory=list)


def suggest_companies(role_description: str, exclude: set[str]) -> list[dict]:
    """Returns verified boards: [{company, slug, ats, jobs, sample_titles}]"""
    proposal = generate_structured(
        f"Role description:\n{role_description[:1000]}\n\n"
        f"Suggest up to {MAX_CANDIDATES} companies.",
        CandidateList,
        system=SUGGEST_SYSTEM,
        max_tokens=1500,
    )

    verified: list[dict] = []
    with httpx.Client(timeout=HTTP_TIMEOUT,
                      headers={"User-Agent": "job-engine/0.1"}) as client:
        for cand in proposal.candidates[:MAX_CANDIDATES]:
            slug = cand.slug.strip().lower()
            if not slug or slug in exclude:
                continue
            hit = _probe_greenhouse(client, slug) or _probe_lever(client, slug)
            if hit:
                hit["company"] = cand.company
                verified.append(hit)
    log.info("suggester: %d proposed, %d verified",
             len(proposal.candidates), len(verified))
    return verified


def _probe_greenhouse(client: httpx.Client, slug: str) -> dict | None:
    try:
        resp = client.get(GREENHOUSE_URL.format(slug=slug))
        if resp.status_code != 200:
            return None
        jobs = resp.json().get("jobs", [])
        return {
            "slug": slug, "ats": "greenhouse", "jobs": len(jobs),
            "sample_titles": [j.get("title", "") for j in jobs[:3]],
        }
    except Exception:
        return None


def _probe_lever(client: httpx.Client, slug: str) -> dict | None:
    try:
        resp = client.get(LEVER_URL.format(slug=slug))
        if resp.status_code != 200:
            return None
        jobs = resp.json()
        if not isinstance(jobs, list):
            return None
        return {
            "slug": slug, "ats": "lever", "jobs": len(jobs),
            "sample_titles": [j.get("text", "") for j in jobs[:3]],
        }
    except Exception:
        return None
