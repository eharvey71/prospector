"""Tier 1 adapter: Lever hosted application forms (jobs.lever.co/.../apply).

Lever forms are classic server-rendered HTML — named inputs, native selects,
radio/checkbox lists — so this adapter is structurally simpler than the
Greenhouse one, but the Tier-1 contract is identical: standard questions are
answered deterministically from profile facts with post-selection
verification; any required field that would take judgment (self-assessments,
consent checkboxes, free-text customs) escalates with a screenshot.

Form anatomy driven here:
  input[name=name]        single full-name field (not first/last)
  input[name=email|phone|org|location]
  input[name=resume]      file input styled behind an upload button;
                          set_input_files works on it directly
  textarea[name=comments] "Additional information" — the cover letter goes here
  .application-question   custom questions: text/textarea/select/radio lists,
                          field names like cards[<uuid>][field0]
"""
from __future__ import annotations

import logging
import os
import re

from playwright.async_api import async_playwright

from .base import SubmissionAdapter, SubmissionOutcome
from .common import (
    DRY_RUN,
    confirm_submission,
    decide_standard_answer,
    fetch_resume,
    find_submit_button,
    mark_submit_clicked,
    unmark_submit_clicked,
    option_matches,
    take_screenshot,
)

log = logging.getLogger("adapter.lever")


class LeverAdapter(SubmissionAdapter):
    tier = 1

    async def submit(self, *, job_url, profile, application, posting,
                     uid, app_id) -> SubmissionOutcome:
        shots: list[str] = []
        prof = profile.get("profile", profile)
        letter = (application.get("letter") or {}).get("text", "")
        answers = application.get("screeningAnswers") or {}
        name = (profile.get("name") or prof.get("name") or "").strip()
        location = (profile.get("location") or prof.get("location") or "")
        work_auth = (profile.get("work_auth") or prof.get("work_auth")
                     or answers.get("work_auth") or "")
        current_org = next(
            (w.get("company", "") for w in (profile.get("work_history")
             or prof.get("work_history") or []) if not w.get("end")), "")
        screeners = profile.get("screeners") or prof.get("screeners") or {}

        # Everything filled or left open, reported back on escalation so the
        # human finishes the form from a checklist.
        sheet: list[dict] = []

        def note(field_label: str, value, status: str = "filled") -> None:
            sheet.append({"field": field_label, "value": value, "status": status})

        apply_url = job_url.rstrip("/")
        if not apply_url.endswith("/apply"):
            apply_url += "/apply"

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(apply_url, wait_until="domcontentloaded", timeout=45_000)

                if await page.locator("input[name='name']").count() == 0:
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        reason="no recognizable Lever form on page",
                    )

                # --- core fields (Lever uses one full-name input) ---
                email = profile.get("email") or prof.get("email", "")
                phone = prof.get("phone", "")
                if await self._fill(page, "input[name='name']", name):
                    note("Full name", name)
                if await self._fill(page, "input[name='email']", email):
                    note("Email", email)
                if await self._fill(page, "input[name='phone']", phone):
                    note("Phone", phone)
                if await self._fill(page, "input[name='org']", current_org):
                    note("Current company", current_org)
                # Location is a typeahead; fill the text and dismiss any
                # suggestion dropdown — Lever accepts the raw text.
                if await self._fill(page, "input[name='location']", location):
                    await page.keyboard.press("Escape")
                    note("Location", location)

                # --- resume ---
                resume_path = await fetch_resume(uid, name, application.get("resume_path"))
                resume_input = page.locator("input[name='resume']").first
                if resume_path and await resume_input.count() > 0:
                    await resume_input.set_input_files(resume_path)
                    note("Resume", os.path.basename(resume_path))
                    # Lever uploads the file async and shows a success mark;
                    # give it a moment but don't fail the run over the badge.
                    try:
                        await page.locator(
                            ".resume-upload-success, [class*='upload'][class*='success']"
                        ).first.wait_for(state="visible", timeout=10_000)
                    except Exception:
                        log.info("no upload-success badge; continuing")
                elif not resume_path and await self._is_required(resume_input):
                    shots.append(await take_screenshot(page, uid, app_id, "no_resume"))
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots,
                        reason="form requires a resume but none is on file",
                    )

                # --- cover letter -> Additional information ---
                if letter:
                    if await self._fill(page, "textarea[name='comments']", letter):
                        note("Cover letter (Additional information)",
                             "entered (full text on this card)")

                # --- custom questions: selects + radio lists only ---
                answered, unanswerable = await self._handle_questions(
                    page, location=location, work_auth=work_auth,
                    screeners=screeners,
                )
                for lbl, ans in answered:
                    note(lbl, ans)
                log.info("questions answered=%s unanswerable=%s",
                         [l for l, _ in answered], unanswerable)

                # --- required fields still empty -> escalate ---
                unmapped = await self._unmapped_required(page)
                if unmapped or unanswerable:
                    remaining = unanswerable + [u for u in unmapped if u not in unanswerable]
                    for u in remaining:
                        note(u, None, "needs_you")
                    shots.append(await take_screenshot(page, uid, app_id, "unmapped"))
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots, fill_sheet=sheet,
                        reason="required fields need a human: "
                               + ", ".join(remaining[:8]),
                    )

                shots.append(await take_screenshot(page, uid, app_id, "pre_submit"))

                if DRY_RUN:
                    log.info("DRY RUN — not submitting %s", apply_url)
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots, fill_sheet=sheet,
                        reason="dry run: form fully filled, submission skipped "
                               "(set SUBMIT_DRY_RUN=false to go live)",
                    )

                submit_btn = await find_submit_button(page, ["#btn-submit"])
                if submit_btn is None:
                    shots.append(await take_screenshot(page, uid, app_id, "no_submit_button"))
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots,
                        reason="filled the form but could not identify Lever's "
                               "Submit button — finish it by hand",
                    )
                # Point of no return: record the click BEFORE making it, then
                # undo the record if the click provably didn't happen.
                await mark_submit_clicked(uid, app_id)
                try:
                    await submit_btn.click()
                except Exception:
                    await unmark_submit_clicked(uid, app_id)
                    raise
                await page.wait_for_load_state("networkidle", timeout=30_000)

                shots.append(await take_screenshot(page, uid, app_id, "post_submit"))
                verdict, why = await confirm_submission(
                    page, title=posting.get("title", ""),
                    company=posting.get("company", ""))

                if verdict == "submitted":
                    return SubmissionOutcome(success=True, tier=self.tier,
                                             clicked_submit=True, screenshots=shots)
                # Both remaining verdicts escalate — clicked_submit blocks any
                # retry — but with wording matched to the fix the human makes.
                if verdict == "not_submitted":
                    reason = (f"the form did not go through ({why}) — open the "
                              f"posting and finish the submission by hand")
                else:
                    reason = (f"submitted, but the result page was inconclusive "
                              f"({why}) — check the screenshot and your email "
                              f"before resubmitting")
                return SubmissionOutcome(
                    success=False, tier=self.tier, escalate=True,
                    clicked_submit=True, screenshots=shots, reason=reason,
                )
            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Custom questions. Lever renders each one inside an
    # .application-question block: a label plus a native select, a
    # radio/checkbox list, or a free-text input. Selects and radios with a
    # deterministic answer get filled and verified; everything else is
    # reported (if required) for escalation. Checkbox questions and consent
    # boxes are never answered — agreeing to something is a human's call.
    # ------------------------------------------------------------------

    async def _handle_questions(self, page, *, location: str, work_auth: str,
                                screeners: dict | None = None,
                                ) -> tuple[list[tuple[str, str]], list[str]]:
        answered: list[tuple[str, str]] = []   # (label, answer given)
        unanswerable: list[str] = []

        blocks = page.locator(
            ".application-question, li:has(.application-label)"
        )
        n = await blocks.count()
        for i in range(n):
            block = blocks.nth(i)
            try:
                if not await block.is_visible():
                    continue
                # Resume/file blocks are handled in submit(), not here.
                if await block.locator("input[type='file']").count() > 0:
                    continue
                label = (await block.locator(
                    ".application-label, label"
                ).first.text_content() or "").strip() or f"question {i+1}"
                short = re.sub(r"[✱*]", "", label).strip()[:60]
                required = "✱" in label or "*" in label or \
                    await block.locator("[required]").count() > 0

                select = block.locator("select").first
                radios = block.locator("input[type='radio']")
                texts = block.locator(
                    "input[type='text'], input[type='number'], textarea"
                ).first

                answer = decide_standard_answer(label, location, work_auth, screeners)

                if await select.count() > 0:
                    if answer is None:
                        if required:
                            unanswerable.append(short)
                        continue
                    if await self._pick_select(select, answer):
                        answered.append((short, answer))
                    else:
                        unanswerable.append(short + " (could not verify selection)")

                elif await radios.count() > 0:
                    if answer is None:
                        if required:
                            unanswerable.append(short)
                        continue
                    if await self._pick_radio(block, radios, answer):
                        answered.append((short, answer))
                    else:
                        unanswerable.append(short + " (could not verify selection)")

                elif await texts.count() > 0:
                    # Free-text custom question: no deterministic answer.
                    if required and not (await texts.input_value()):
                        unanswerable.append(short)

                else:
                    # Checkbox lists, consent boxes, anything else: a human's
                    # call when required.
                    if required:
                        unanswerable.append(short)
            except Exception as exc:
                log.warning("question %d failed: %s", i, exc)
                unanswerable.append(f"question {i+1} (interaction failed)")

        return answered, unanswerable

    async def _pick_select(self, select, answer: str) -> bool:
        """Choose the option matching the answer, then verify the select's
        displayed value actually shows it."""
        options = await select.evaluate(
            "el => [...el.options].map(o => o.textContent.trim())")
        candidates = [t for t in options if option_matches(answer, t)]
        if not candidates:
            return False
        exact = [t for t in candidates
                 if re.fullmatch(rf"{re.escape(answer)}[.,!]?", t, re.I)]
        pick = exact[0] if len(exact) == 1 else min(candidates, key=len)
        await select.select_option(label=pick)
        shown = await select.evaluate(
            "el => el.selectedOptions[0]?.textContent.trim() || ''")
        verified = answer.lower() in shown.lower()
        if not verified:
            # Deselect entirely — index 0 could be a real option, not a
            # placeholder, and a wrong answer is worse than an escalation.
            await select.evaluate(
                "el => { el.selectedIndex = -1;"
                " el.dispatchEvent(new Event('change', {bubbles: true})); }")
        return verified

    async def _pick_radio(self, block, radios, answer: str) -> bool:
        """Check the radio whose label/value matches the answer; verify with
        is_checked. Radios can't be unchecked, but a failed match means we
        never clicked one in the first place."""
        n = await radios.count()
        candidates: list[tuple[int, str]] = []
        for j in range(n):
            radio = radios.nth(j)
            text = (await radio.evaluate(
                "el => (el.closest('label')?.textContent || el.value || '').trim()"
            )) or ""
            if option_matches(answer, text):
                candidates.append((j, text))
        if not candidates:
            return False
        exact = [(j, t) for j, t in candidates
                 if re.fullmatch(rf"{re.escape(answer)}[.,!]?", t, re.I)]
        j_pick = exact[0][0] if len(exact) == 1 else \
            min(candidates, key=lambda c: len(c[1]))[0]
        await radios.nth(j_pick).check()
        return await radios.nth(j_pick).is_checked()

    # ------------------------------------------------------------------

    async def _fill(self, page, selector: str, value: str) -> bool:
        """Fill and verify: read the value back and log a mismatch loudly.
        A fill that silently doesn't stick (page JS clearing it, a decoy
        element matching first) otherwise only surfaces as a confusing
        entry in the escalation reason."""
        if not value:
            return False
        loc = page.locator(selector).first
        n = await loc.count()
        if n == 0:
            log.info("fill %s: no match on page", selector)
            return False
        await loc.fill(value)
        shown = await loc.input_value()
        if shown != value:
            log.warning("fill %s did NOT stick: wanted %r, field shows %r "
                        "(matches on page: %d)", selector, value, shown,
                        await page.locator(selector).count())
            return False
        log.info("fill %s ok", selector)
        return True

    async def _is_required(self, locator) -> bool:
        if await locator.count() == 0:
            return False
        return await locator.evaluate(
            "el => el.required || el.getAttribute('aria-required') === 'true'"
            " || !!el.closest('li,div')?.querySelector('.required')")

    async def _unmapped_required(self, page) -> list[str]:
        # Label lookup walks up to the question block first, so an input
        # nested in its own wrapper div still reports as "LinkedIn URL"
        # rather than its internal name "urls[LinkedIn]".
        return await page.evaluate("""
            () => [...new Set([...document.querySelectorAll(
                'input[required], textarea[required], select[required]')]
                .filter(el => el.type !== 'file' && el.type !== 'hidden'
                        && el.type !== 'radio' && el.type !== 'checkbox'
                        && el.offsetParent !== null
                        && !el.value)
                .map(el => {
                    const c = el.closest('.application-question')
                        || el.closest('li') || el.closest('div');
                    const lbl = c?.querySelector('.application-label, label');
                    return (lbl?.textContent || el.name || el.placeholder
                            || 'unknown').replace(/[✱*]/g, '').trim().slice(0, 60);
                }))]
        """)
