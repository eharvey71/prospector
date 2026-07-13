"""Track a company by name: resolve its ATS automatically and register it.

The user shouldn't have to know whether a company is on Greenhouse, Lever, or
something else. Given a name, this:
  1. asks the LLM for the official board slug(s), careers URL, and known ATS,
  2. probes Greenhouse/Lever (deterministic, reliable) — a hit is full
     automation and goes straight onto the watchlist,
  3. otherwise fetches the careers page, fingerprints the ATS from the final
     URL + page markers, and either registers a crawlable career page or
     reports honestly that submission will be manual (login-wall ATSes).

Every outcome is reported back so the UI can tell the user exactly what they
got — never a silent "added" that quietly finds nothing.
"""
from __future__ import annotations

import logging
import re

import httpx
from google.cloud import firestore
from pydantic import BaseModel, Field

from llm import generate_structured
from suggest import _probe_greenhouse, _probe_lever

log = logging.getLogger("track")

HTTP_TIMEOUT = 12.0

# ATS host/marker -> (label, can the app submit end-to-end?)
ATS_FINGERPRINTS: list[tuple[str, str, bool]] = [
    ("greenhouse.io", "greenhouse", True),
    ("lever.co", "lever", True),
    ("myworkdayjobs.com", "workday", False),
    ("wd1.myworkday", "workday", False),
    ("icims.com", "icims", False),
    ("ashbyhq.com", "ashby", True),
    ("smartrecruiters.com", "smartrecruiters", True),
    ("workable.com", "workable", True),
    ("jobvite.com", "jobvite", False),
    ("taleo.net", "taleo", False),
    ("brassring.com", "brassring", False),
    ("successfactors.com", "successfactors", False),
    ("phenom", "phenom", False),
]

RESOLVE_SYSTEM = """You identify how a company runs its job applications. \
Given a company name, provide: likely Greenhouse/Lever board slugs (lowercase, \
no spaces — often the company name), the company's main careers page URL, and \
the ATS vendor you believe they use if you know it. Only state facts you're \
reasonably confident about; leave a field empty otherwise."""


class CompanyResolution(BaseModel):
    slugs: list[str] = Field(default_factory=list)
    careers_url: str | None = None
    known_ats: str | None = None


def _slug_variants(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]+", "", name.lower())
    hyphen = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    nosuffix = re.sub(r"(inc|llc|ltd|plc|corp|corporation|company|co)$", "", base)
    return [s for s in dict.fromkeys([base, hyphen, nosuffix]) if s]


def track_company(db: firestore.Client, uid: str, name: str) -> dict:
    resolution = generate_structured(
        f"Company: {name}", CompanyResolution, system=RESOLVE_SYSTEM, max_tokens=400,
    )
    slugs = list(dict.fromkeys(_slug_variants(name) + [
        re.sub(r"[^a-z0-9-]+", "", s.lower()) for s in resolution.slugs if s
    ]))

    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "job-engine/0.1"}) as client:
        # 1. Deterministic board probe — the reliable, fully-automated path.
        for slug in slugs:
            for probe, kind in ((_probe_greenhouse, "greenhouse"),
                                (_probe_lever, "lever")):
                hit = probe(client, slug)
                if hit and hit.get("jobs", 0) >= 0:
                    _add_to_watchlist(db, uid, kind, slug)
                    return _result(name, "auto", kind, slug,
                                   f"On {kind} ({hit['jobs']} open roles) — fully "
                                   f"automated, added to your watchlist.")

        # 2. Careers page: fetch, fingerprint the ATS.
        careers = resolution.careers_url
        if careers:
            ats, submittable, final_url = _fingerprint_page(client, careers)
            # A careers page may itself embed a Greenhouse/Lever board.
            if ats in ("greenhouse", "lever"):
                slug = _slug_from_url(final_url, ats)
                if slug and (probe := (_probe_greenhouse if ats == "greenhouse"
                                       else _probe_lever)(client, slug)):
                    _add_to_watchlist(db, uid, ats, slug)
                    return _result(name, "auto", ats, slug,
                                   f"Careers page runs on {ats} — fully automated.")

            # Workday exposes a public JSON API — register the careers URL in
            # the workday list so discovery pulls its jobs; submission stays
            # manual (no adapter -> escalates with the prepared application).
            if ats == "workday":
                _add_to_watchlist(db, uid, "workday", final_url)
                return _result(name, "manual", ats, final_url,
                               "On Workday. Jobs will be discovered and drafted "
                               "automatically; you submit the final step yourself "
                               "(Workday requires an account).")

            _add_to_watchlist(db, uid, "custom", careers)
            if ats and not submittable:
                return _result(name, "manual", ats, careers,
                               f"Uses {ats} (login-wall). The careers page is "
                               f"tracked for drafting; you submit yourself. If it's "
                               f"JavaScript-only, paste specific job URLs instead.")
            if ats:
                return _result(name, "tracked", ats, careers,
                               f"Uses {ats}. Tracking the careers page; submission "
                               f"attempted automatically (Tier 2).")
            return _result(name, "tracked", "unknown", careers,
                           "Tracking the careers page. If it's JavaScript-rendered "
                           "the crawler may miss jobs — paste specific job URLs then.")

    return _result(name, "not_found", None, None,
                   "Couldn't find a board or careers page automatically. Paste a "
                   "job URL from this company into the review queue instead.")


def _fingerprint_page(client: httpx.Client, url: str) -> tuple[str | None, bool, str]:
    if not url.startswith("http"):
        url = "https://" + url
    try:
        resp = client.get(url)
        final = str(resp.url)
        hay = (final + " " + resp.text[:200_000]).lower()
    except httpx.HTTPError as exc:
        log.warning("careers fetch %s failed: %s", url, exc)
        return None, False, url
    for marker, label, submittable in ATS_FINGERPRINTS:
        if marker in hay:
            return label, submittable, final
    return None, False, final


def _slug_from_url(url: str, ats: str) -> str | None:
    pat = (r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9-]+)"
           if ats == "greenhouse" else r"jobs\.lever\.co/([a-z0-9-]+)")
    m = re.search(pat, url.lower())
    return m.group(1) if m else None


def _add_to_watchlist(db: firestore.Client, uid: str, field: str, value: str) -> None:
    ref = (db.collection("users").document(uid)
           .collection("watchlist").document("companies"))
    snap = ref.get().to_dict() or {}
    existing = snap.get(field, [])
    if value not in existing:
        ref.set({field: existing + [value]}, merge=True)


def _result(company: str, status: str, ats, target, detail: str) -> dict:
    return {"company": company, "status": status, "ats": ats,
            "target": target, "detail": detail}
