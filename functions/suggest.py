"""Company suggester: role description -> verified job boards.

The LLM proposes candidate companies for the role; nothing it says is
trusted directly. Every candidate is probed against public board APIs —
Greenhouse and Lever (fully automated submission) plus Workday (discovery +
drafting; submission stays manual) — and only live boards are returned.
The client adds chosen ones to the watchlist; suggestions never write
anything themselves.

Comprehensiveness comes from breadth at every step: several slug guesses
per company (an LLM's first guess is often wrong even when the board
exists), multiple proposal rounds until enough boards verify, and parallel
probing so the rounds fit the request budget.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx
from pydantic import BaseModel, Field

from llm import generate_structured

log = logging.getLogger("suggest")

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

ROUND_CANDIDATES = 25     # companies per proposal round
MAX_ROUNDS = 3
TARGET_VERIFIED = 12      # stop proposing once this many boards verified
MAX_SLUGS_PER_COMPANY = 5
HTTP_TIMEOUT = 6.0
PROBE_THREADS = 8

SUGGEST_SYSTEM = """You suggest employers for a job seeker. Given a role \
description, list organizations genuinely likely to hire for it — across \
every relevant sector: companies, non-profits, NGOs, foundations, \
healthcare and education organizations, research institutes, government \
contractors. Mix well-known and less obvious employers; never repeat \
organizations listed as already tried.

SECTOR FIDELITY: the role description is authoritative. Stay strictly \
within the kind of organization it describes — if it says "non-profit \
medical association", suggest medical associations, societies, and health \
non-profits, NOT tech companies or consultancies that merely employ \
similar roles. Any seeker background provided is context for seniority \
and specialty only; never let it override the described sector.

For each organization provide:
- slugs: up to 4 guesses at their job-board slug (lowercase, no spaces; \
usually the organization's name, sometimes abbreviated or hyphenated — \
"Wikimedia Foundation" -> ["wikimedia", "wikimediafoundation", \
"wikimedia-foundation"]). Greenhouse and Lever are common far beyond tech.
- workday_url: the full careers URL (https://<tenant>.wd<N>.myworkdayjobs\
.com/<Site>) ONLY if you're confident they use Workday (common for large \
enterprises, universities, hospitals, and big non-profits); otherwise null."""


class CompanyCandidate(BaseModel):
    company: str
    slugs: list[str] = Field(default_factory=list)
    workday_url: str | None = None


class CandidateList(BaseModel):
    candidates: list[CompanyCandidate] = Field(default_factory=list)


def _slug_variants(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]+", "", name.lower())
    hyphen = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    nosuffix = re.sub(r"(inc|llc|ltd|plc|corp|corporation|company|co|org"
                      r"|foundation|association)$", "", base)
    return [s for s in dict.fromkeys([base, hyphen, nosuffix]) if s]


def suggest_companies(role_description: str, exclude: set[str],
                      location: str = "", remote_only: bool = False,
                      wanted_locations: list[str] | None = None,
                      work_mode: str = "local_or_remote",
                      titles: list[str] | None = None,
                      skills: list[str] | None = None) -> dict:
    """Returns {"companies": verified, "unverified": [names]}.

    companies: [{company, slug, ats, jobs, sample_titles}] — live boards
    (for ats=workday, slug is the full myworkdayjobs URL).
    unverified: organizations the LLM proposed that have no board on a
    supported ATS — still likely fits; the UI offers Track (the full
    resolver, which handles careers-page fingerprinting) for each."""
    situation = ""
    if titles or skills:
        situation += ("\nSeeker background (context only — the role "
                      "description above wins on any conflict):")
    if titles:
        situation += f"\n  target titles: {', '.join(titles[:6])}"
    if skills:
        situation += f"\n  skills: {', '.join(skills[:12])}"
    if location:
        situation += f"\nCandidate location: {location}"
    places = ", ".join(wanted_locations or [])
    if remote_only or work_mode == "remote_only":
        situation += "\nRemote roles only: prefer remote-friendly organizations."
    elif places:
        # Named places make geography a REQUIREMENT, not a nudge — this is
        # how a new grad in Richmond gets CoStar and VCU Health instead of
        # famous national brands with no local hiring.
        situation += (
            f"\nGEOGRAPHY IS A HARD REQUIREMENT: the seeker works in "
            f"{places}. Suggest organizations that genuinely hire there — "
            "headquartered there, or major local employers (health systems, "
            "universities, banks, utilities, insurers, school systems, "
            "state/local government and contractors), or national employers "
            "with a substantial office there. Prefer the distinctly local "
            "over famous names with no local presence."
            + ("\nFully-remote-friendly organizations also qualify."
               if work_mode == "local_or_remote" else ""))
    elif location:
        situation += ("\nPrefer organizations with a presence near the "
                      "candidate or strong remote cultures.")

    verified: list[dict] = []
    unverified: list[str] = []     # proposed, sector-relevant, but no board found
    tried: list[str] = []          # company names already proposed (any outcome)
    seen_boards: set[str] = {s.lower() for s in exclude}

    with httpx.Client(timeout=HTTP_TIMEOUT,
                      headers={"User-Agent": "job-engine/0.1"}) as client:
        for round_no in range(MAX_ROUNDS):
            if len(verified) >= TARGET_VERIFIED:
                break
            already = ("\nAlready tried — do NOT repeat: "
                       + ", ".join(tried[-80:])) if tried else ""
            proposal = generate_structured(
                f"Role description:\n{role_description[:1000]}\n{situation}"
                f"{already}\n\nSuggest up to {ROUND_CANDIDATES} organizations.",
                CandidateList,
                system=SUGGEST_SYSTEM,
                max_tokens=2500,
            )
            fresh = [c for c in proposal.candidates[:ROUND_CANDIDATES]
                     if c.company and c.company.lower() not in
                     {t.lower() for t in tried}]
            tried += [c.company for c in fresh]

            with ThreadPoolExecutor(max_workers=PROBE_THREADS) as pool:
                results = list(pool.map(
                    lambda c: _verify_candidate(client, c, seen_boards), fresh))
            for cand, hit in zip(fresh, results):
                if hit and hit["slug"].lower() not in seen_boards:
                    seen_boards.add(hit["slug"].lower())
                    verified.append(hit)
                elif hit is None:
                    unverified.append(cand.company)
            log.info("suggester round %d: %d proposed, %d verified so far",
                     round_no + 1, len(fresh), len(verified))

    return {"companies": verified, "unverified": unverified[:12]}


def _verify_candidate(client: httpx.Client, cand: CompanyCandidate,
                      exclude: set[str]) -> dict | None:
    slugs = [re.sub(r"[^a-z0-9-]+", "", s.lower()) for s in cand.slugs if s]
    slugs = list(dict.fromkeys(slugs + _slug_variants(cand.company)))
    for slug in slugs[:MAX_SLUGS_PER_COMPANY]:
        if not slug or slug in exclude:
            continue
        hit = _probe_greenhouse(client, slug) or _probe_lever(client, slug)
        if hit:
            hit["company"] = cand.company
            return hit
    if cand.workday_url:
        hit = _probe_workday(client, cand.workday_url)
        if hit:
            hit["company"] = cand.company
            return hit
    return None


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


def _probe_workday(client: httpx.Client, url: str) -> dict | None:
    """Verify a myworkdayjobs URL via the public CxS API (the same one
    discovery crawls). Returns slug=THE URL — that's what the watchlist's
    workday field stores."""
    from discovery import _parse_workday_url
    parsed = _parse_workday_url(url.strip())
    if not parsed:
        return None
    host, tenant, site = parsed
    try:
        resp = client.post(
            f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
            json={"appliedFacets": {}, "limit": 3, "offset": 0, "searchText": ""})
        if resp.status_code != 200:
            return None
        data = resp.json()
        batch = data.get("jobPostings", [])
        if not batch:
            return None
        return {
            "slug": f"https://{host}/{site}",
            "ats": "workday",
            "jobs": data.get("total", len(batch)),
            "sample_titles": [j.get("title", "") for j in batch[:3]],
        }
    except Exception:
        return None
