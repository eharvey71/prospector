"""Attended mode: open a job form in a VISIBLE browser on your machine, let
the engine fill everything it can while you watch, then hand you the
keyboard. You answer the human-only questions, click Submit yourself, and
the script records the result. Because you're present, captchas and login
walls stop being blockers.

Prereqs (once):
    pip install playwright google-cloud-firestore google-cloud-storage pydantic
    playwright install chromium
    gcloud auth application-default login
    export STORAGE_BUCKET=<your bucket>   # same value as in functions/.env;
                                          # enables auto-attaching your resume

Usage:
    python attend.py                  # pick from your needs_human queue
    python attend.py --state in_review  # or work a reviewed app by hand
    python attend.py --app <APP_ID>   # skip the picker
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "worker"))

from google.cloud import firestore  # noqa: E402

PROJECT = "job-engine-c8f9c"
UID = "ewa4WvPd5CT5SIJCmHlWAqbWaqt2"
PICKABLE_STATES = ["needs_human", "in_review", "approved", "failed"]


def pick_application(db, uid: str, state: str, app_id: str | None):
    apps_ref = db.collection("users").document(uid).collection("applications")
    if app_id:
        snap = apps_ref.document(app_id).get()
        if not snap.exists:
            sys.exit(f"application {app_id} not found")
        return snap.id, snap.to_dict()

    candidates = list(apps_ref.where("state", "==", state).stream())
    if not candidates:
        sys.exit(f"no applications in state {state!r}")

    print(f"\nApplications in {state!r}:")
    rows = []
    for i, snap in enumerate(candidates):
        data = snap.to_dict()
        posting = (db.collection("jobPostings")
                   .document(data.get("posting_id", "")).get().to_dict() or {})
        rows.append((snap.id, data, posting))
        print(f"  [{i}] {posting.get('title', '?')} @ {posting.get('company', '?')}"
              f"  (score {((data.get('match') or {}).get('score', '?'))})")
    choice = input("\nWhich one? ")
    try:
        app_id, data, _ = rows[int(choice)]
    except (ValueError, IndexError):
        sys.exit("no valid choice — nothing done")
    return app_id, data


def print_cheat_sheet(app_data: dict) -> None:
    letter = (app_data.get("letter") or {}).get("text", "")
    if letter:
        path = Path(tempfile.mkdtemp()) / "cover_letter.txt"
        path.write_text(letter)
        print(f"\n--- COVER LETTER (also saved to {path}) ---\n{letter}\n")

    answers = app_data.get("screeningAnswers") or {}
    entries = [(k, v) for k, v in answers.items() if k != "extra" and v]
    entries += list((answers.get("extra") or {}).items())
    if entries:
        print("--- PREPARED SCREENING ANSWERS ---")
        for k, v in entries:
            print(f"  {k}: {v}")

    sheet = (app_data.get("submission") or {}).get("fill_sheet") or []
    needs = [e for e in sheet if e.get("status") != "filled"]
    if needs:
        print("\n--- QUESTIONS ONLY YOU CAN ANSWER ---")
        for e in needs:
            line = f"  {e.get('field')}"
            if e.get("suggestion"):
                line += f"  (suggested: {e['suggestion']})"
            print(line)
    print()


async def fill_form(page, source: str, url: str, profile: dict, app_data: dict,
                    uid: str) -> None:
    """Best-effort deterministic fill using the worker's adapter helpers.
    Unknown ATSes just get the page opened — use the cheat sheet."""
    from adapters.common import fetch_resume

    prof = profile.get("profile", profile)
    name = (profile.get("name") or prof.get("name") or "").strip()
    letter = (app_data.get("letter") or {}).get("text", "")
    answers = app_data.get("screeningAnswers") or {}
    location = profile.get("location") or prof.get("location") or ""
    work_auth = (profile.get("work_auth") or prof.get("work_auth")
                 or answers.get("work_auth") or "")
    screeners = profile.get("screeners") or {}

    if source == "greenhouse":
        from adapters.greenhouse import GreenhouseAdapter
        gh = GreenhouseAdapter()
        apply_btn = page.locator("a:has-text('Apply'), button:has-text('Apply')").first
        if await apply_btn.count() > 0 and await page.locator("#first_name").count() == 0:
            await apply_btn.click()
            await page.wait_for_load_state("domcontentloaded")
        if await page.locator("#first_name").count() == 0:
            print("! no Greenhouse form found — fill by hand with the cheat sheet")
            return
        first, _, last = name.partition(" ")
        await gh._fill(page, "#first_name", first)
        await gh._fill(page, "#last_name", last)
        await gh._fill(page, "#email", profile.get("email") or prof.get("email", ""))
        await gh._fill(page, "#phone", prof.get("phone", ""))
        resume = await fetch_resume(uid, name, app_data.get("resume_path"))
        if resume:
            await gh._attach_file(page, resume, section_hint="resume")
        if letter:
            await gh._enter_cover_letter(page, letter)
        answered, unanswerable = await gh._handle_dropdowns(
            page, location=location, work_auth=work_auth, screeners=screeners)
        print(f"filled {len(answered)} dropdowns; left for you: "
              f"{', '.join(unanswerable) or 'none'}")

    elif source == "lever":
        from adapters.lever import LeverAdapter
        lv = LeverAdapter()
        if await page.locator("input[name='name']").count() == 0:
            print("! no Lever form found — fill by hand with the cheat sheet")
            return
        await lv._fill(page, "input[name='name']", name)
        await lv._fill(page, "input[name='email']",
                       profile.get("email") or prof.get("email", ""))
        await lv._fill(page, "input[name='phone']", prof.get("phone", ""))
        if await lv._fill(page, "input[name='location']", location):
            await page.keyboard.press("Escape")
        resume = await fetch_resume(uid, name, app_data.get("resume_path"))
        resume_input = page.locator("input[name='resume']").first
        if resume and await resume_input.count() > 0:
            await resume_input.set_input_files(resume)
        if letter:
            await lv._fill(page, "textarea[name='comments']", letter)
        answered, unanswerable = await lv._handle_questions(
            page, location=location, work_auth=work_auth, screeners=screeners)
        print(f"answered {len(answered)} questions; left for you: "
              f"{', '.join(unanswerable) or 'none'}")

    else:
        print(f"source {source!r} has no deterministic adapter — the page is "
              "open; use the cheat sheet above.")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uid", default=UID)
    ap.add_argument("--app", help="application id (skips the picker)")
    ap.add_argument("--state", default="needs_human", choices=PICKABLE_STATES)
    args = ap.parse_args()

    db = firestore.Client(project=PROJECT)
    app_id, app_data = pick_application(db, args.uid, args.state, args.app)
    posting = (db.collection("jobPostings")
               .document(app_data.get("posting_id", "")).get().to_dict() or {})
    profile = db.collection("users").document(args.uid).get().to_dict() or {}
    url = str(posting.get("url", ""))
    source = posting.get("source", "unknown")

    print(f"\n=== {posting.get('title')} @ {posting.get('company')} ===")
    print(f"url: {url}\nsource: {source}")
    print_cheat_sheet(app_data)

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()
        # Lever applications live at /apply; mirror the worker's behavior.
        goto = url.rstrip("/") + "/apply" \
            if source == "lever" and not url.rstrip("/").endswith("/apply") else url
        await page.goto(goto, wait_until="domcontentloaded", timeout=60_000)
        try:
            await fill_form(page, source, url, profile, app_data, args.uid)
        except Exception as exc:
            print(f"! fill hit a snag ({exc}) — finish by hand, everything "
                  "you need is in the cheat sheet above")

        print("\nThe browser is yours: review every field, answer what's "
              "left, and click Submit on the page.")
        done = input("Type 'y' once you've submitted (anything else = leave "
                     "the application unchanged): ").strip().lower()
        await browser.close()

    if done == "y":
        now = datetime.now(timezone.utc)
        (db.collection("users").document(args.uid)
         .collection("applications").document(app_id).update({
             "state": "submitted",
             "updatedAt": now,
             "submission.confirmedAt": now,
             "stateHistory": firestore.ArrayUnion([{
                 "state": "submitted", "ts": now,
                 "note": "submitted by hand via attended mode",
             }]),
         }))
        print("recorded as submitted ✓")
    else:
        print("left unchanged — the card stays in its queue")


if __name__ == "__main__":
    asyncio.run(main())
