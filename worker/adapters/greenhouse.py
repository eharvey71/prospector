"""Tier 1 adapter v2: Greenhouse hosted application forms.

Upgrades over v1:
  1. Attach/Enter-manually patterns: modern Greenhouse boards hide the
     resume file input behind an "Attach" button (file chooser) and the
     cover letter textarea behind an "Enter manually" button. v2 drives both.
  2. Deterministic dropdown answering: standard Greenhouse questions
     (country of residence, location, visa sponsorship) are answered from
     profile facts via pattern matching — no LLM, no guessing. Questions
     that require judgment (self-assessments, legal restrictions) still
     escalate.
  3. Resume attaches under a human-readable filename built from the
     profile name (recruiters see the filename).

The Tier-1 contract is unchanged: any required field this adapter cannot
answer deterministically -> escalate with a screenshot, never improvise.
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

log = logging.getLogger("adapter.greenhouse")


class GreenhouseAdapter(SubmissionAdapter):
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
        screeners = profile.get("screeners") or prof.get("screeners") or {}

        # Everything filled or left open, reported back on escalation so the
        # human finishes the form from a checklist.
        sheet: list[dict] = []

        def note(field_label: str, value, status: str = "filled") -> None:
            sheet.append({"field": field_label, "value": value, "status": status})

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(job_url, wait_until="domcontentloaded", timeout=45_000)

                apply_btn = page.locator(
                    "a:has-text('Apply'), button:has-text('Apply')"
                ).first
                if await apply_btn.count() > 0 and await page.locator("#first_name").count() == 0:
                    await apply_btn.click()
                    await page.wait_for_load_state("domcontentloaded")

                if await page.locator("#first_name").count() == 0:
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        reason="no recognizable Greenhouse form on page",
                    )

                # --- core fields ---
                first, _, last = name.partition(" ")
                email = profile.get("email") or prof.get("email", "")
                phone = prof.get("phone", "")
                if await self._fill(page, "#first_name", first):
                    note("First name", first)
                if await self._fill(page, "#last_name", last):
                    note("Last name", last)
                if await self._fill(page, "#email", email):
                    note("Email", email)
                if await self._fill(page, "#phone", phone):
                    note("Phone", phone)

                # --- resume: direct input, else Attach button/file chooser ---
                resume_path = await fetch_resume(uid, name, application.get("resume_path"))
                if resume_path:
                    if await self._attach_file(page, resume_path, section_hint="resume"):
                        note("Resume", os.path.basename(resume_path))

                # --- cover letter: textarea, else Enter-manually reveal ---
                if letter:
                    if await self._enter_cover_letter(page, letter):
                        note("Cover letter", "entered (full text on this card)")

                # --- deterministic dropdown answering ---
                answered, unanswerable = await self._handle_dropdowns(
                    page, location=location, work_auth=work_auth,
                    screeners=screeners,
                )
                for lbl, ans in answered:
                    note(lbl, ans)
                log.info("dropdowns answered=%s unanswerable=%s",
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
                    log.info("DRY RUN — not submitting %s", job_url)
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots, fill_sheet=sheet,
                        reason="dry run: form fully filled, submission skipped "
                               "(set SUBMIT_DRY_RUN=false to go live)",
                    )

                submit_btn = await find_submit_button(page, ["#submit_app"])
                if submit_btn is None:
                    shots.append(await take_screenshot(page, uid, app_id, "no_submit_button"))
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots,
                        reason="filled the form but could not identify Greenhouse's "
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
                # Either verdict below escalates: clicked_submit stops the
                # worker retrying (which would file the application a second
                # time) — a human verifies instead. "not_submitted" gets its
                # own wording because the fix is different: the form is still
                # on screen, so the human finishes it rather than checks email.
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
    # File attachment (resume): handles both visible inputs and the
    # Attach-button file-chooser pattern.
    # ------------------------------------------------------------------

    async def _attach_file(self, page, path: str, section_hint: str) -> bool:
        # Direct visible input first (legacy boards)
        direct = page.locator("input[type='file']:visible").first
        if await direct.count() > 0:
            await direct.set_input_files(path)
            return True

        # Attach button within the section whose label mentions the hint
        section = page.locator(
            f"div:has(label:text-matches('{section_hint}', 'i')), "
            f"div:has(div:text-matches('{section_hint}', 'i'))"
        ).locator("button:has-text('Attach')").first
        btn = section if await section.count() > 0 else \
            page.locator("button:has-text('Attach')").first

        if await btn.count() == 0:
            return False

        # Attach buttons either trigger a native file chooser or reveal a
        # hidden input. Try the chooser; fall back to a hidden input.
        try:
            async with page.expect_file_chooser(timeout=5_000) as fc_info:
                await btn.click()
            chooser = await fc_info.value
            await chooser.set_files(path)
            return True
        except Exception:
            hidden = page.locator("input[type='file']").first
            if await hidden.count() > 0:
                await hidden.set_input_files(path)
                return True
        return False

    # ------------------------------------------------------------------
    # Cover letter: plain textarea, else "Enter manually" reveal.
    # ------------------------------------------------------------------

    async def _enter_cover_letter(self, page, letter: str) -> bool:
        direct = page.locator(
            "textarea[name*='cover_letter'], #cover_letter_text"
        ).first
        if await direct.count() > 0:
            await direct.fill(letter)
            return True

        # Find the Enter-manually button nearest a "Cover Letter" label.
        buttons = page.locator("button:has-text('Enter manually')")
        n = await buttons.count()
        for i in range(n):
            btn = buttons.nth(i)
            # Heuristic: is this button inside/after a cover-letter section?
            container_text = await btn.evaluate(
                "el => el.closest('div')?.parentElement?.textContent || ''"
            )
            if re.search(r"cover\s*letter", container_text, re.I) or n == 1:
                await btn.click()
                revealed = page.locator("textarea:visible").last
                if await revealed.count() > 0:
                    await revealed.fill(letter)
                    return True
        # If there are exactly two Enter-manually buttons (resume + letter),
        # the second is conventionally the cover letter.
        if n == 2:
            await buttons.nth(1).click()
            revealed = page.locator("textarea:visible").last
            if await revealed.count() > 0:
                await revealed.fill(letter)
                return True
        return False

    # ------------------------------------------------------------------
    # Deterministic dropdown answering.
    # Greenhouse custom questions render as combobox widgets. We read each
    # question's label, decide an answer from profile facts if the pattern
    # is unambiguous, and select it. Everything else is reported back as
    # unanswerable (by label, so the escalation reason is human-readable).
    # ------------------------------------------------------------------

    async def _handle_dropdowns(self, page, *, location: str, work_auth: str,
                                screeners: dict | None = None,
                                ) -> tuple[list[tuple[str, str]], list[str]]:
        answered: list[tuple[str, str]] = []   # (label, answer given)
        unanswerable: list[str] = []

        combos = page.locator(
            "[role='combobox'], select[required], "
            "div[class*='select'] input[aria-autocomplete]"
        )
        n = await combos.count()
        for i in range(n):
            combo = combos.nth(i)
            try:
                if not await combo.is_visible():
                    continue
                # Skip intl-tel-input phone country selectors — decoration on
                # the Phone field, not a screening question.
                if await combo.evaluate("el => !!el.closest('.iti, [class*=intl-tel]')"):
                    continue
                label = (await combo.evaluate("""
                    el => {
                        // 1) aria-labelledby -> referenced elements' text
                        const ids = el.getAttribute('aria-labelledby');
                        if (ids) {
                            const t = ids.split(/\\s+/)
                                .map(id => document.getElementById(id)?.textContent || '')
                                .join(' ').trim();
                            if (t) return t;
                        }
                        // 2) aria-label directly
                        const al = el.getAttribute('aria-label');
                        if (al && al.trim()) return al.trim();
                        // 3) label[for=id]
                        if (el.id) {
                            const lbl = document.querySelector(
                                'label[for="' + CSS.escape(el.id) + '"]');
                            if (lbl && lbl.textContent.trim()) return lbl.textContent.trim();
                        }
                        // 4) walk up to 6 ancestors, take the first <label>
                        //    or heading-ish text block found in that subtree
                        let node = el;
                        for (let d = 0; d < 6 && node; d++) {
                            node = node.parentElement;
                            const lbl = node?.querySelector('label');
                            if (lbl && lbl.textContent.trim()) return lbl.textContent.trim();
                        }
                        // 5) last resort: nearest preceding text node content
                        let prev = el.closest('div')?.previousElementSibling;
                        for (let d = 0; d < 3 && prev; d++) {
                            const t = prev.textContent?.trim();
                            if (t && t.length > 4 && t.length < 300) return t;
                            prev = prev.previousElementSibling;
                        }
                        return '';
                    }
                """)) or f"dropdown {i+1}"
                required = "*" in label or await combo.evaluate(
                    "el => el.required || el.getAttribute('aria-required') === 'true'"
                    " || el.closest('div')?.textContent.includes('*')"
                )

                answer = decide_standard_answer(label, location, work_auth, screeners)
                if answer is None:
                    if required:
                        unanswerable.append(label.replace("*", "").strip()[:60])
                    continue

                tag = await combo.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    await combo.select_option(label=re.compile(rf"^{re.escape(answer)}", re.I))
                    answered.append((label.replace("*", "").strip()[:60], answer))
                    continue

                # Combobox widgets: select, then VERIFY, else revert+escalate.
                # A wrong answer is far worse than an escalation.
                await combo.click()
                await combo.type(answer, delay=30)
                picked = False
                options = page.locator("[role='option']")
                candidates: list[tuple[int, str]] = []
                for j in range(await options.count()):
                    opt = options.nth(j)
                    # Only VISIBLE menu options — the page also contains ~230
                    # hidden [role=option] entries inside the Phone field's
                    # intl-tel-input widget, which must never be candidates.
                    if not await opt.is_visible():
                        continue
                    if await opt.evaluate("el => !!el.closest('.iti, [class*=intl-tel]')"):
                        continue
                    raw = ((await opt.text_content()) or "").strip()
                    # Normalize numbered options: '1. United States of America'
                    text = re.sub(r"^\s*\d+[.)]\s*", "", raw)
                    if option_matches(answer, text):
                        candidates.append((j, text))
                if len(candidates) == 1:
                    await options.nth(candidates[0][0]).click()
                    picked = True
                elif len(candidates) > 1:
                    # Prefer an exact match ('No' among 'No'/'No, but...');
                    # otherwise take the SHORTEST candidate — the option with
                    # the least qualification beyond the intended answer
                    # ('United States of America' over
                    #  'United States Minor Outlying Islands').
                    exact = [(j, t) for j, t in candidates
                             if re.fullmatch(rf"{re.escape(answer)}[.,!]?", t, re.I)]
                    j_pick = exact[0][0] if len(exact) == 1 else \
                        min(candidates, key=lambda c: len(c[1]))[0]
                    await options.nth(j_pick).click()
                    picked = True

                # VERIFY: read the widget's displayed value. React-select
                # renders the choice into a '[class*=single-value]' element
                # inside the CONTROL container. Walk starts at the PARENT —
                # the input's own class (select__input) would false-match.
                # The re-render after the option click is ASYNC — a single
                # immediate read races it and reverts good selections, so
                # poll for up to ~2.5s before giving up.
                verified = False
                shown = ""
                if picked:
                    for _ in range(8):
                        shown = (await combo.evaluate("""
                            el => {
                                let n = el.parentElement;
                                let control = null;
                                for (let d = 0; d < 6 && n; d++) {
                                    if (n.className && /control/.test(String(n.className))) {
                                        control = n; break;
                                    }
                                    n = n.parentElement;
                                }
                                const scope = control || el.closest('div')?.parentElement || el.parentElement;
                                const sv = scope?.querySelector("[class*='single-value'], [class*='selected']");
                                return (sv?.textContent || scope?.textContent || '').trim();
                            }
                        """)) or ""
                        if answer.lower() in shown.lower():
                            verified = True
                            break
                        await page.wait_for_timeout(300)
                log.info("dropdown %r: answer=%r picked=%s shown=%r verified=%s",
                         label[:50], answer, picked, shown[:80], verified)

                if picked and verified:
                    answered.append((label.replace("*", "").strip()[:60], answer))
                else:
                    # Revert whatever state we left and hand it to the human.
                    await page.keyboard.press("Escape")
                    try:
                        clear_btn = combo.locator(
                            "xpath=ancestor::div[2]//*[@aria-label='Clear' or @aria-label='Remove']"
                        ).first
                        if await clear_btn.count() > 0:
                            await clear_btn.click(timeout=2_000)
                    except Exception:
                        pass
                    unanswerable.append(
                        label.replace("*", "").strip()[:60] + " (could not verify selection)"
                    )
            except Exception as exc:
                log.warning("dropdown %d failed: %s", i, exc)
                unanswerable.append(f"dropdown {i+1} (interaction failed)")

        return answered, unanswerable

    # ------------------------------------------------------------------

    async def _fill(self, page, selector: str, value: str) -> bool:
        if not value:
            return False
        loc = page.locator(selector).first
        if await loc.count() == 0:
            return False
        await loc.fill(value)
        return True

    async def _unmapped_required(self, page) -> list[str]:
        # Combobox inner inputs stay value-less even when an option is
        # selected, so they're excluded here — dropdowns are fully accounted
        # for (answered or escalated) by _handle_dropdowns.
        return await page.evaluate("""
            () => [...document.querySelectorAll(
                'input[required], textarea[required]')]
                .filter(el => !el.value && el.type !== 'file'
                        && el.offsetParent !== null
                        && el.getAttribute('role') !== 'combobox'
                        && !el.getAttribute('aria-autocomplete')
                        && !el.closest("[class*='select'],[role='combobox']"))
                .map(el => {
                    const c = el.closest('div');
                    const lbl = c?.querySelector('label');
                    return (lbl?.textContent || el.name || el.id
                            || el.placeholder || 'unknown').trim().slice(0, 60);
                })
        """)
