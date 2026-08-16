"""Helpers shared by the Tier-1 adapters.

Filling is deterministic: option matching, standard-question answering
from profile facts, resume download, screenshot upload. No LLM calls in
the fill path — an adapter that can't answer from these facts escalates.
The one LLM call here is confirm_submission(), the post-submit judge; it
runs AFTER the click and can only downgrade an outcome, never fill a form.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

log = logging.getLogger("adapter.common")

DRY_RUN = os.environ.get("SUBMIT_DRY_RUN", "true").lower() == "true"
BUCKET = os.environ.get("STORAGE_BUCKET", "")

# Word-bounded on purpose: a bare ", ca" substring would classify
# "Toronto, Canada" as California (and ", co" would claim Colombia).
US_LOCATION_RX = re.compile(
    r"\bunited states\b|\busa\b"
    r"|,\s*(va|ca|ny|tx|wa|ma|pa|il|ga|nc|fl|oh|co|or|md)\b", re.I)
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


def decide_standard_answer(label: str, location: str, work_auth: str,
                           screeners: dict | None = None) -> str | None:
    """Answer the ATS-standard screening questions from profile facts, or
    None for anything that requires judgment (self-assessments, legal
    restrictions, prior employment).

    screeners: the profile's ScreenerFacts dict (open_to_relocation,
    onsite_ok). A missing/None fact means "not stated" -> escalate."""
    q = label.lower()
    loc = location.lower()
    auth = work_auth.lower()
    s = screeners or {}
    in_us = bool(US_LOCATION_RX.search(loc))
    us_authorized = any(h in auth for h in US_CITIZEN_HINTS)

    def yes_no(fact):
        return None if fact is None else ("Yes" if fact else "No")

    if re.search(r"country of residence|country.*(located|reside)|where are you.*based|currently based", q):
        return "United States" if in_us else None
    if re.search(r"require.*sponsorship|sponsorship.*visa|visa.*sponsor|require.*employment visa", q):
        return "No" if us_authorized else None
    if re.search(r"authorized to work|legally.*work", q):
        return "Yes" if us_authorized else None
    if re.search(r"\bcitizen(ship)?\b", q):
        # Yes/no citizenship questions only; status dropdowns whose options
        # don't yes/no-match will fail option matching and escalate safely.
        if "citizen" in auth:
            return "Yes"
        if "green card" in auth or "permanent resident" in auth:
            return "No"
        return None
    if re.search(r"\brelocat", q):
        return yes_no(s.get("open_to_relocation"))
    if re.search(r"\bin[\s-]?person\b|\bin[\s-]?office\b|\bon[\s-]?site\b"
                 r"|\bhybrid\b|work(ing)?\s(from|in)\s(an|the|one of our)\s?offices?", q):
        return yes_no(s.get("onsite_ok"))
    return None


async def fetch_resume(uid: str, display_name: str,
                       tailored_path: str | None = None) -> str | None:
    """Download the resume; attach it under a recruiter-friendly filename.
    Prefers the application's tailored resume when one exists, falling back
    to the uploaded users/{uid}/resume.pdf."""
    if not BUCKET:
        return None
    try:
        from google.cloud import storage
        bucket = storage.Client().bucket(BUCKET)
        blob = bucket.blob(tailored_path) if tailored_path else None
        if blob is None or not blob.exists():
            blob = bucket.blob(f"users/{uid}/resume.pdf")
        if not blob.exists():
            return None
        nice = re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_") or "Candidate"
        path = os.path.join(tempfile.mkdtemp(), f"{nice}_Resume.pdf")
        blob.download_to_filename(path)
        return path
    except Exception:
        log.exception("resume fetch failed for uid=%s", uid)
        return None


SUBMIT_TEXT = re.compile(r"\bsubmit\b|\bsend application\b|\bapply\b", re.I)
# For buttons WITHOUT type=submit (React forms wire plain buttons with JS
# handlers): the whole label must be a submit phrase, not merely contain
# one — "Apply now" yes, "Apply filters" or "Search jobs" never.
PLAIN_SUBMIT_RX = re.compile(
    r"^\s*(submit(\s+(application|now))?|apply(\s+(now|for this job))?"
    r"|send(\s+(application|my application))?)\s*$", re.I)

# ---------------------------------------------------------------------------
# Post-submit confirmation: deterministic checks, then an LLM judge.
# ---------------------------------------------------------------------------

# Deliberately narrow: these phrases essentially never appear before a
# successful submit. A bare "thank you" is NOT enough — job descriptions
# say "thank you for your interest", and on a validation-failure page the
# description is still visible. Anything short of these goes to the judge.
CONFIRM_RX = re.compile(
    r"thank you for (applying|your application)"
    r"|application (has been|was) (submitted|received|sent)"
    r"|we('| ha)ve received your application", re.I)
CONFIRM_URL_RX = re.compile(r"/(thanks|thank-you|confirmation|submitted)\b", re.I)

JUDGE_SYSTEM = """You are an impartial auditor of a job-application \
submission. You are given the text of the page shown immediately after the \
Submit button was clicked. Decide from the evidence alone — do not assume \
success. Verdicts:
- "submitted": the page clearly indicates the application was received \
(confirmation message, receipt, what-happens-next text).
- "not_submitted": the application form is still displayed, especially with \
validation errors or required-field messages.
- "unclear": anything else.
confidence is 0-1. reason is one short sentence quoting the decisive \
evidence."""


class SubmitVerdict(BaseModel):
    verdict: Literal["submitted", "not_submitted", "unclear"]
    confidence: float = 0.0
    reason: str = ""


async def confirm_submission(page, *, title: str = "", company: str = ""
                             ) -> tuple[str, str]:
    """Did the click actually file the application?

    Returns (verdict, reason): 'submitted' | 'not_submitted' | 'unclear'.
    Free deterministic checks first; the LLM judge only reads the page when
    they fail. The judge is independent of the fill logic on purpose — the
    code that did the work doesn't get to grade it. Any judge failure or
    hesitation degrades to 'unclear' (escalate), never to success or retry.
    """
    # Text from EVERY frame: on embedded boards the confirmation renders
    # inside the same iframe the form lived in, not the top document.
    texts = []
    for fr in getattr(page, "frames", None) or [page]:
        try:
            t = await fr.evaluate(
                "() => document.body ? document.body.innerText : ''")
            if t:
                texts.append(t)
        except Exception:
            pass
    body = "\n".join(texts)
    if CONFIRM_URL_RX.search(page.url or ""):
        return "submitted", f"confirmation URL ({page.url})"
    if CONFIRM_RX.search(body):
        return "submitted", "confirmation phrase on page"
    if not body.strip():
        return "unclear", "post-submit page had no readable text"

    try:
        from llm import generate_structured
        v = generate_structured(
            f"Application submitted for: {title} at {company}\n"
            f"Page URL after clicking Submit: {page.url}\n\n"
            f"PAGE TEXT:\n{body[:6000]}",
            SubmitVerdict,
            system=JUDGE_SYSTEM,
            max_tokens=300,
        )
    except Exception as exc:
        log.warning("submission judge unavailable: %s", exc)
        return "unclear", f"judge unavailable ({type(exc).__name__})"
    if v.verdict == "submitted" and v.confidence < 0.7:
        return "unclear", f"judge unsure ({v.confidence:.2f}): {v.reason}"
    return v.verdict, v.reason or v.verdict


async def find_submit_button(page, preferred: list[str]):
    """The form's real submit control, or None.

    A selector list like "button[type=submit], #submit_app" does NOT try
    them in order — .first is first in DOM ORDER, so a cookie banner or
    newsletter button earlier on the page wins. This walks explicit
    priorities instead: the ATS's known id, then a visible submit-type
    button whose text actually says submit/apply, then any visible
    submit-type button.
    """
    for sel in preferred:
        loc = page.locator(sel).first
        if await loc.count() > 0 and await loc.is_visible():
            return loc
    for sel in ("button[type='submit']", "input[type='submit']"):
        loc = page.locator(sel)
        for i in range(min(await loc.count(), 20)):
            b = loc.nth(i)
            if not await b.is_visible():
                continue
            label = ((await b.text_content()) or "") + " " \
                + ((await b.get_attribute("value")) or "")
            if SUBMIT_TEXT.search(label):
                return b
    # Plain buttons wired with JS handlers (React forms often skip
    # type=submit entirely): accept only an exact submit-phrase label.
    for sel in ("button", "[role='button']"):
        loc = page.locator(sel)
        for i in range(min(await loc.count(), 40)):
            b = loc.nth(i)
            if not await b.is_visible():
                continue
            if PLAIN_SUBMIT_RX.match(((await b.text_content()) or "").strip()):
                return b
    for sel in ("button[type='submit']", "input[type='submit']"):
        loc = page.locator(sel).first
        if await loc.count() > 0 and await loc.is_visible():
            return loc
    return None


async def unmark_submit_clicked(uid: str, app_id: str) -> None:
    """Undo the marker when the click provably did NOT happen (Playwright
    raises rather than clicking blind), so the job stays safely retryable."""
    try:
        from google.cloud import firestore
        (firestore.Client().collection("users").document(uid)
         .collection("applications").document(app_id)
         .update({"submission.submitClickedAt": firestore.DELETE_FIELD}))
    except Exception:
        log.exception("could not clear submit-click marker uid=%s app=%s",
                      uid, app_id)


async def mark_submit_clicked(uid: str, app_id: str) -> bool:
    """Record — BEFORE clicking a real Submit button — that this application
    has been filed. If the worker then crashes or times out, the retried
    delivery sees this marker and escalates instead of submitting again.

    Returns False when the write fails, and the adapter must then NOT
    click: an unrecorded click is exactly the double-submission hole the
    marker exists to close. Not clicking is always safe — the attempt
    becomes a retryable failure."""
    try:
        from google.cloud import firestore
        (firestore.Client().collection("users").document(uid)
         .collection("applications").document(app_id)
         .update({"submission.submitClickedAt": datetime.now(timezone.utc)}))
        return True
    except Exception:
        log.exception("could not record submit-click marker uid=%s app=%s",
                      uid, app_id)
        return False


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
