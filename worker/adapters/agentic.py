"""Tier 2 adapter: LLM-guided form filling for unknown ATSes.

Where Tier 1 knows a form's shape in advance, Tier 2 discovers it: the page's
visible form controls are inventoried (label, kind, options, required), and an
LLM maps each control to a value drawn ONLY from the user's profile facts and
pre-approved screening answers. The adapter — not the LLM — then performs
every fill deterministically and verifies each one by reading the value back.

The escalation contract is unchanged from Tier 1, enforced structurally:
  - The LLM must justify every value with the profile fact it came from and
    must put anything unsupported into cannot_answer.
  - Values for selects/radios must exactly equal one of the enumerated
    options; anything else is dropped and escalated.
  - Checkboxes (consent boxes especially) are never answered.
  - Any required control left unverified escalates with a screenshot.
  - Captchas are never solved; their presence is reported in the escalation.
"""
from __future__ import annotations

import logging
import os
import re

from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

from .base import SubmissionAdapter, SubmissionOutcome
from .common import DRY_RUN, fetch_resume, mark_submit_clicked, take_screenshot

log = logging.getLogger("adapter.agentic")

MAX_FIELDS = 60

PLAN_SYSTEM = """You fill job application forms on behalf of a candidate. \
You are given the form's controls and the candidate's profile facts. For \
each control, either provide a value or list its id in cannot_answer.

Hard rules:
- Use ONLY the provided facts. Every fill must cite the fact it came from \
in its justification. If a value would require judgment, opinion, \
self-assessment, legal interpretation, or any fact not present, put the \
control in cannot_answer instead. Guessing is the worst possible failure.
- For select/radio controls the value must EXACTLY match one of the listed \
options, character for character.
- Never answer checkbox controls of any kind.
- cover_letter kind controls: use the provided cover letter text verbatim."""


class FieldFill(BaseModel):
    control_id: int
    value: str
    justification: str


class FormPlan(BaseModel):
    fills: list[FieldFill] = Field(default_factory=list)
    cannot_answer: list[int] = Field(default_factory=list)


# JS: tag every fillable control with data-je-id and return an inventory the
# LLM can reason about. Runs once per page.
INVENTORY_JS = """
() => {
    const seen = new Set();
    const out = [];
    const labelFor = (el) => {
        if (el.labels && el.labels.length) return el.labels[0].textContent;
        const al = el.getAttribute('aria-label');
        if (al) return al;
        let n = el;
        for (let d = 0; d < 5 && n; d++) {
            n = n.parentElement;
            const lbl = n?.querySelector('label, legend, [class*="label"]');
            if (lbl && lbl.textContent.trim()) return lbl.textContent;
        }
        return el.name || el.placeholder || '';
    };
    const controls = [...document.querySelectorAll(
        'input, textarea, select')].filter(el =>
        el.offsetParent !== null && el.type !== 'hidden'
        && el.type !== 'submit' && el.type !== 'button');
    for (const el of controls) {
        // Custom dropdown widgets (react-select and friends): the inner
        // <input> is NOT a text field — typing into it selects nothing,
        // yet reads back as "filled". Classify it separately so it is
        // never plan-filled; required ones escalate honestly.
        const isCombo = el.tagName === 'INPUT' && (
            el.getAttribute('role') === 'combobox'
            || el.hasAttribute('aria-autocomplete')
            || !!el.closest("[role='combobox'],[class*='select']"));
        const kind =
            el.tagName === 'SELECT' ? 'select' :
            el.tagName === 'TEXTAREA' ? 'textarea' :
            el.type === 'file' ? 'file' :
            el.type === 'radio' ? 'radio' :
            el.type === 'checkbox' ? 'checkbox' :
            isCombo ? 'combobox' : 'text';
        // Radios: one entry per GROUP, options from each input's label/value.
        if (kind === 'radio') {
            if (seen.has('radio:' + el.name)) continue;
            seen.add('radio:' + el.name);
            const group = [...document.querySelectorAll(
                `input[type="radio"][name="${CSS.escape(el.name)}"]`)];
            const id = out.length;
            group.forEach(r => r.setAttribute('data-je-id', String(id)));
            const fieldset = el.closest('fieldset, ul, div');
            // Group label: a legend inside the container, else a label in
            // the container's parent that isn't one of the option labels.
            let glabel = fieldset?.querySelector('legend')?.textContent;
            if (!glabel) {
                const lbl = fieldset?.parentElement?.querySelector(
                    'label, [class*="label"]');
                if (lbl && !lbl.querySelector('input')) glabel = lbl.textContent;
            }
            out.push({
                id, kind,
                label: (glabel || el.name).trim().slice(0, 200),
                required: group.some(r => r.required),
                options: group.map(r =>
                    (r.closest('label')?.textContent || r.value).trim()),
            });
            continue;
        }
        const id = out.length;
        el.setAttribute('data-je-id', String(id));
        const label = labelFor(el).trim().slice(0, 200);
        out.push({
            id, kind,
            label,
            required: el.required
                || el.getAttribute('aria-required') === 'true',
            options: kind === 'select'
                ? [...el.options].map(o => o.textContent.trim())
                    .filter(t => t).slice(0, 80)
                : undefined,
            value: (kind === 'text' || kind === 'textarea')
                ? el.value : undefined,
        });
    }
    return out;
}
"""

CAPTCHA_SELECTOR = (
    "iframe[src*='captcha'], iframe[src*='recaptcha'], "
    "iframe[src*='hcaptcha'], iframe[src*='turnstile'], [class*='captcha']"
)


class AgenticAdapter(SubmissionAdapter):
    tier = 2

    async def submit(self, *, job_url, profile, application, posting,
                     uid, app_id) -> SubmissionOutcome:
        shots: list[str] = []
        prof = profile.get("profile", profile)
        letter = (application.get("letter") or {}).get("text", "")
        name = (profile.get("name") or prof.get("name") or "").strip()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(job_url, wait_until="domcontentloaded", timeout=45_000)
                # One hop through an Apply link if the landing page has no form.
                if await page.locator("form input, form textarea").count() == 0:
                    apply_btn = page.locator(
                        "a:has-text('Apply'), button:has-text('Apply')").first
                    if await apply_btn.count() > 0:
                        await apply_btn.click()
                        await page.wait_for_load_state("domcontentloaded")

                inventory = await page.evaluate(INVENTORY_JS)
                if not inventory:
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        reason="no form controls found on page",
                    )
                if len(inventory) > MAX_FIELDS:
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        reason=f"form too large for tier 2 ({len(inventory)} controls)",
                    )

                # Mark cover-letter fields so the plan uses the letter verbatim.
                for c in inventory:
                    if re.search(r"cover\s*letter", c["label"], re.I) \
                            and c["kind"] in ("text", "textarea"):
                        c["kind"] = "cover_letter"

                plan = self._plan(inventory, profile, application, letter)
                filled, failed = await self._execute(page, inventory, plan, letter)

                # Resume: attach to any file input whose label says resume/cv.
                resume_path = await fetch_resume(uid, name, application.get("resume_path"))
                for c in inventory:
                    if c["kind"] == "file" and re.search(
                            r"resume|\bcv\b", c["label"], re.I):
                        if resume_path:
                            await page.locator(
                                f"[data-je-id='{c['id']}']").set_input_files(resume_path)
                            filled.append((c["id"], os.path.basename(resume_path)))
                        elif c["required"]:
                            failed.append((c["id"], "resume required, none on file"))

                # ANY required control not verified-filled escalates — whether
                # the LLM declared it unanswerable, a fill failed, or it was
                # never plannable at all (combobox widgets, checkboxes). The
                # old cannot_answer-based check missed that last group.
                by_id = {c["id"]: c for c in inventory}
                filled_ids = {i for i, _ in filled}
                blockers = [
                    c["label"][:60] or f"control {c['id']}"
                    for c in inventory
                    if c["required"] and c["id"] not in filled_ids
                ]
                captcha = await page.locator(CAPTCHA_SELECTOR).count() > 0
                if captcha:
                    blockers.append("captcha on page")

                # Fill sheet: what was used, and what only the human can do.
                sheet = [{"field": by_id[i]["label"][:60] or f"control {i}",
                          "value": v, "status": "filled"} for i, v in filled]
                sheet += [{"field": b, "value": None, "status": "needs_you"}
                          for b in blockers]

                log.info("tier2 filled=%s failed=%s blockers=%s",
                         sorted(filled_ids), failed, blockers)

                if blockers:
                    shots.append(await take_screenshot(page, uid, app_id, "unmapped"))
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots, fill_sheet=sheet,
                        reason="needs a human: " + ", ".join(blockers[:8]),
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

                # Point of no return: record the click BEFORE making it.
                await mark_submit_clicked(uid, app_id)
                await page.locator(
                    "button[type='submit'], input[type='submit']").first.click()
                await page.wait_for_load_state("networkidle", timeout=30_000)
                confirmed = await page.locator(
                    "text=/thank you|application.*(submitted|received)/i"
                ).count() > 0
                shots.append(await take_screenshot(page, uid, app_id, "post_submit"))
                if confirmed:
                    return SubmissionOutcome(success=True, tier=self.tier,
                                             clicked_submit=True, screenshots=shots)
                return SubmissionOutcome(
                    success=False, tier=self.tier, escalate=True,
                    clicked_submit=True, screenshots=shots,
                    reason="submitted, but no confirmation message was found — "
                           "check the screenshot and your email before resubmitting",
                )
            finally:
                await browser.close()

    # ------------------------------------------------------------------

    def _plan(self, inventory: list[dict], profile: dict,
              application: dict, letter: str) -> FormPlan:
        from llm import generate_structured

        prof = profile.get("profile", profile)
        answers = application.get("screeningAnswers") or {}
        screeners = profile.get("screeners") or prof.get("screeners") or {}
        tri = lambda v: ("not stated — put such questions in cannot_answer"  # noqa: E731
                         if v is None else ("Yes" if v else "No"))
        history = "\n".join(
            f"- {w.get('title')} at {w.get('company')} "
            f"({w.get('start')} to {w.get('end') or 'present'})"
            for w in (profile.get("work_history") or prof.get("work_history") or [])
        )
        education = "\n".join(
            f"- {e.get('degree')}, {e.get('school')}"
            + (f" ({e.get('year')})" if e.get("year") else "")
            for e in (profile.get("education") or prof.get("education") or [])
        )
        facts = f"""CANDIDATE FACTS (the only permitted sources):
Name: {profile.get('name') or prof.get('name') or ''}
Email: {profile.get('email') or prof.get('email') or ''}
Phone: {prof.get('phone') or ''}
Location: {profile.get('location') or prof.get('location') or ''}
Work authorization: {profile.get('work_auth') or prof.get('work_auth') or ''}
Salary target: {profile.get('salary_target') or prof.get('salary_target') or ''}
Open to relocation: {tri(screeners.get('open_to_relocation'))}
Willing to work in-person/onsite/hybrid: {tri(screeners.get('onsite_ok'))}
Work history:
{history}
Education:
{education or "(none listed)"}

PRE-APPROVED SCREENING ANSWERS:
Why this company: {answers.get('why_company') or ''}
Salary: {answers.get('salary') or ''}
Work authorization: {answers.get('work_auth') or ''}

COVER LETTER (use verbatim for cover_letter controls):
{letter[:3000]}"""

        # Comboboxes can't be filled by the plan (typing into the inner input
        # selects nothing); checkboxes are never answered; files are handled
        # separately. Leaving them out of the listing keeps the LLM from
        # planning fills that _execute would reject anyway.
        controls = "\n".join(
            f"[{c['id']}] kind={c['kind']} required={c['required']} "
            f"label={c['label']!r}"
            + (f" options={c['options']}" if c.get("options") else "")
            for c in inventory
            if c["kind"] not in ("combobox", "checkbox", "file")
        )
        return generate_structured(
            f"{facts}\n\nFORM CONTROLS:\n{controls}\n\nProduce the plan.",
            FormPlan,
            system=PLAN_SYSTEM,
            max_tokens=3000,
        )

    async def _execute(self, page, inventory: list[dict], plan: FormPlan,
                       letter: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
        """Perform the plan's fills deterministically; verify every one.
        Returns (filled as (id, value used), failed as (id, reason))."""
        by_id = {c["id"]: c for c in inventory}
        filled: list[tuple[int, str]] = []
        failed: list[tuple[int, str]] = []

        for f in plan.fills:
            c = by_id.get(f.control_id)
            if c is None:
                continue
            loc = page.locator(f"[data-je-id='{c['id']}']")
            try:
                shown_value = f.value
                if c["kind"] in ("text", "textarea", "cover_letter"):
                    value = letter if c["kind"] == "cover_letter" else f.value
                    await loc.first.fill(value)
                    ok = (await loc.first.input_value()) == value
                    if c["kind"] == "cover_letter":
                        shown_value = "entered (full text on this card)"
                elif c["kind"] == "select":
                    if f.value not in (c.get("options") or []):
                        failed.append((c["id"], "value not among options"))
                        continue
                    await loc.first.select_option(label=f.value)
                    shown = await loc.first.evaluate(
                        "el => el.selectedOptions[0]?.textContent.trim() || ''")
                    ok = shown == f.value
                elif c["kind"] == "radio":
                    if f.value not in (c.get("options") or []):
                        failed.append((c["id"], "value not among options"))
                        continue
                    idx = c["options"].index(f.value)
                    await loc.nth(idx).check()
                    ok = await loc.nth(idx).is_checked()
                else:
                    # checkbox / file / anything else: never LLM-driven
                    failed.append((c["id"], f"kind {c['kind']} not fillable by plan"))
                    continue

                log.info("fill [%d] %r = %r (%s) verified=%s",
                         c["id"], c["label"][:40], f.value[:60],
                         f.justification[:80], ok)
                if ok:
                    filled.append((c["id"], shown_value))
                else:
                    failed.append((c["id"], "verification failed"))
            except Exception as exc:
                log.warning("fill [%d] failed: %s", c["id"], exc)
                failed.append((c["id"], str(exc)[:80]))

        return filled, failed
