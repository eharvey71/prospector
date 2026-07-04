"""Tier 1 adapter: Greenhouse hosted application forms.

Greenhouse job pages (boards.greenhouse.io / job-boards.greenhouse.io) render
a consistent form: #first_name, #last_name, #email, #phone, a resume upload,
and per-job custom questions. Strategy:

  1. Fill every field we can map deterministically.
  2. Attach the resume from Cloud Storage.
  3. Fill the cover letter textarea if present.
  4. Screenshot before submit, submit, screenshot confirmation.
  5. Any unmapped REQUIRED field -> escalate rather than guess.

Escalating on unknowns is the whole Tier-1 contract: this adapter never
improvises, so a submission it reports as success is trustworthy.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from .base import SubmissionAdapter, SubmissionOutcome

log = logging.getLogger("adapter.greenhouse")

DRY_RUN = os.environ.get("SUBMIT_DRY_RUN", "true").lower() == "true"
BUCKET = os.environ.get("STORAGE_BUCKET", "")


class GreenhouseAdapter(SubmissionAdapter):
    tier = 1

    async def submit(self, *, job_url, profile, application, posting,
                     uid, app_id) -> SubmissionOutcome:
        shots: list[str] = []
        prof = profile.get("profile", profile)  # tolerate both doc shapes
        letter = (application.get("letter") or {}).get("text", "")
        answers = application.get("screeningAnswers") or {}

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(job_url, wait_until="domcontentloaded", timeout=45_000)

                # Some GH pages put the form behind an "Apply" button.
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
                name = (profile.get("name") or prof.get("name") or "").split(" ", 1)
                first, last = name[0], (name[1] if len(name) > 1 else "")
                await self._fill(page, "#first_name", first)
                await self._fill(page, "#last_name", last)
                await self._fill(page, "#email", profile.get("email") or prof.get("email", ""))
                await self._fill(page, "#phone", prof.get("phone", ""))

                # --- resume upload ---
                resume_path = await self._fetch_resume(uid)
                if resume_path:
                    upload = page.locator("input[type='file']").first
                    if await upload.count() > 0:
                        await upload.set_input_files(resume_path)

                # --- cover letter ---
                cl = page.locator(
                    "textarea[name*='cover_letter'], #cover_letter_text"
                ).first
                if await cl.count() > 0 and letter:
                    await cl.fill(letter)

                # --- required custom questions we can't map -> escalate ---
                unmapped = await self._unmapped_required(page)
                if unmapped:
                    shots.append(await self._shot(page, uid, app_id, "unmapped"))
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots,
                        reason=f"required fields need a human: {', '.join(unmapped[:5])}",
                    )

                shots.append(await self._shot(page, uid, app_id, "pre_submit"))

                if DRY_RUN:
                    log.info("DRY RUN — not submitting %s", job_url)
                    return SubmissionOutcome(
                        success=False, tier=self.tier, escalate=True,
                        screenshots=shots,
                        reason="dry run: form filled, submission skipped "
                               "(set SUBMIT_DRY_RUN=false to go live)",
                    )

                await page.locator(
                    "button[type='submit'], input[type='submit'], #submit_app"
                ).first.click()
                await page.wait_for_load_state("networkidle", timeout=30_000)

                confirmed = await page.locator(
                    "text=/thank you|application.*(submitted|received)/i"
                ).count() > 0
                shots.append(await self._shot(page, uid, app_id, "post_submit"))

                if confirmed:
                    return SubmissionOutcome(success=True, tier=self.tier, screenshots=shots)
                return SubmissionOutcome(
                    success=False, tier=self.tier, screenshots=shots,
                    reason="submitted but no confirmation text found",
                )
            finally:
                await browser.close()

    # ------------------------------------------------------------------

    async def _fill(self, page, selector: str, value: str) -> None:
        if not value:
            return
        loc = page.locator(selector).first
        if await loc.count() > 0:
            await loc.fill(value)

    async def _unmapped_required(self, page) -> list[str]:
        """Names of required inputs still empty after our mapping pass."""
        return await page.evaluate("""
            () => [...document.querySelectorAll(
                'input[required], textarea[required], select[required]')]
                .filter(el => !el.value && el.type !== 'file')
                .map(el => el.name || el.id || el.placeholder || 'unknown')
        """)

    async def _fetch_resume(self, uid: str) -> str | None:
        """Download users/{uid}/resume.pdf from Cloud Storage to a temp file."""
        if not BUCKET:
            return None
        try:
            from google.cloud import storage
            blob = storage.Client().bucket(BUCKET).blob(f"users/{uid}/resume.pdf")
            if not blob.exists():
                return None
            path = os.path.join(tempfile.mkdtemp(), "resume.pdf")
            blob.download_to_filename(path)
            return path
        except Exception:
            log.exception("resume fetch failed for uid=%s", uid)
            return None

    async def _shot(self, page, uid: str, app_id: str, label: str) -> str:
        """Screenshot -> Cloud Storage; returns the storage path."""
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
