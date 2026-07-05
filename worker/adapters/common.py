"""Helpers shared by the Tier-1 adapters.

Everything here is deterministic: option matching, standard-question
answering from profile facts, resume download, screenshot upload. No LLM
calls — an adapter that can't answer from these facts escalates.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone

log = logging.getLogger("adapter.common")

DRY_RUN = os.environ.get("SUBMIT_DRY_RUN", "true").lower() == "true"
BUCKET = os.environ.get("STORAGE_BUCKET", "")

US_LOCATION_HINTS = (
    "united states", "usa", ", va", ", ca", ", ny", ", tx", ", wa", ", ma",
    ", pa", ", il", ", ga", ", nc", ", fl", ", oh", ", co", ", or", ", md",
)
US_CITIZEN_HINTS = ("us citizen", "u.s. citizen", "citizen", "green card",
                    "permanent resident", "authorized to work")


def option_matches(answer: str, option_text: str) -> bool:
    """Strict option matching. 'No' must match 'No' or 'No.' — and must NEVER
    match 'Yes, but not one of the visas listed here'. Rules:
      1. Exact match (modulo trailing punctuation) always wins.
      2. Otherwise the option must START with the answer as a whole word,
         and the yes/no polarity of both strings must agree.
    """
    a = answer.strip().lower()
    t = option_text.strip().lower()
    if re.fullmatch(rf"{re.escape(a)}[.,!]?", t):
        return True
    if not re.match(rf"^{re.escape(a)}\b", t):
        return False
    a_yes, t_yes = a.startswith("yes"), t.startswith("yes")
    a_no, t_no = a.startswith("no"), t.startswith("no")
    return a_yes == t_yes and a_no == t_no


def decide_standard_answer(label: str, location: str, work_auth: str) -> str | None:
    """Answer the ATS-standard screening questions from profile facts, or
    None for anything that requires judgment (self-assessments, legal
    restrictions, prior employment)."""
    q = label.lower()
    loc = location.lower()
    auth = work_auth.lower()
    in_us = any(h in loc for h in US_LOCATION_HINTS)
    us_authorized = any(h in auth for h in US_CITIZEN_HINTS)

    if re.search(r"country of residence|country.*(located|reside)|where are you.*based|currently based", q):
        return "United States" if in_us else None
    if re.search(r"require.*sponsorship|sponsorship.*visa|visa.*sponsor", q):
        return "No" if us_authorized else None
    if re.search(r"authorized to work|legally.*work", q):
        return "Yes" if us_authorized else None
    return None


async def fetch_resume(uid: str, display_name: str) -> str | None:
    """Download the resume; attach it under a recruiter-friendly filename."""
    if not BUCKET:
        return None
    try:
        from google.cloud import storage
        blob = storage.Client().bucket(BUCKET).blob(f"users/{uid}/resume.pdf")
        if not blob.exists():
            return None
        nice = re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_") or "Candidate"
        path = os.path.join(tempfile.mkdtemp(), f"{nice}_Resume.pdf")
        blob.download_to_filename(path)
        return path
    except Exception:
        log.exception("resume fetch failed for uid=%s", uid)
        return None


async def take_screenshot(page, uid: str, app_id: str, label: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    storage_path = f"users/{uid}/screenshots/{app_id}/{ts}_{label}.png"
    png = await page.screenshot(full_page=True)
    if BUCKET:
        try:
            from google.cloud import storage
            storage.Client().bucket(BUCKET).blob(storage_path).upload_from_string(
                png, content_type="image/png")
        except Exception:
            log.exception("screenshot upload failed")
    return storage_path
