"""Job discovery from public ATS JSON endpoints.

No browsers, no scraping heroics — Greenhouse and Lever expose clean JSON per
company board. The crawl reads each user's watchlist, unions the company
lists, fetches boards, and upserts postings keyed by a deterministic hash so
re-runs are natural dedup.

Watchlist doc shape (users/{uid}/watchlist/companies):
    { "greenhouse": ["anthropic", "stripe"], "lever": ["plaid"] }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from google.cloud import firestore

from schemas import AtsType, JobPosting

log = logging.getLogger("discovery")

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{board}?mode=json"

HTTP_TIMEOUT = 20.0


def run_discovery(db: firestore.Client) -> int:
    """Full crawl. Returns number of postings upserted."""
    boards = _collect_watchlists(db)
    count = 0
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": "job-engine/0.1"}) as client:
        for board in sorted(boards.get("greenhouse", set())):
            count += _upsert_all(db, _fetch_greenhouse(client, board))
        for board in sorted(boards.get("lever", set())):
            count += _upsert_all(db, _fetch_lever(client, board))
    log.info("discovery complete: %d postings upserted", count)
    return count


def _collect_watchlists(db: firestore.Client) -> dict[str, set[str]]:
    boards: dict[str, set[str]] = {"greenhouse": set(), "lever": set()}
    for doc in db.collection_group("watchlist").stream():
        data = doc.to_dict() or {}
        boards["greenhouse"].update(data.get("greenhouse", []))
        boards["lever"].update(data.get("lever", []))
    return boards


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
