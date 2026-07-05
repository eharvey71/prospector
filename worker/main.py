"""Cloud Run submission worker.

Receives Cloud Tasks at POST /submit, routes by ATS type:
  Tier 1  deterministic Playwright adapter (greenhouse, lever)
  Tier 2  agentic adapter for unknown ATSes (LLM plans, code fills+verifies)
  Tier 3  escalate to NEEDS_HUMAN with pre-filled answers

HTTP semantics for Cloud Tasks: 2xx = done (success OR handled escalation);
5xx = retry with backoff. Escalations return 200 on purpose so the queue
doesn't hammer a form we already know needs a human.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from google.cloud import firestore

from adapters import get_adapter
from adapters.base import SubmissionOutcome
from schemas import AppState, SubmitTask
from state_machine import advance

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")

MAX_ATTEMPTS = int(os.environ.get("MAX_SUBMIT_ATTEMPTS", "3"))

db: firestore.Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    db = firestore.Client()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/submit")
async def submit(request: Request) -> dict:
    assert db is not None
    task = SubmitTask.model_validate_json(await request.body())
    log.info("submit task uid=%s app=%s ats=%s", task.uid, task.app_id, task.ats_type)

    # queued -> submitting (idempotency gate: a duplicate delivery finds the
    # app already past QUEUED and stops here)
    if not advance(db, task.uid, task.app_id, AppState.QUEUED, AppState.SUBMITTING):
        log.info("app %s not in QUEUED; duplicate delivery, acking", task.app_id)
        return {"status": "duplicate"}

    adapter = get_adapter(task.ats_type)
    if adapter is None:
        _escalate(task, "no adapter for this ATS")
        return {"status": "needs_human"}

    app_ref = (
        db.collection("users").document(task.uid)
        .collection("applications").document(task.app_id)
    )
    app_data = app_ref.get().to_dict() or {}
    user_data = db.collection("users").document(task.uid).get().to_dict() or {}
    posting = db.collection("jobPostings").document(task.posting_id).get().to_dict() or {}

    attempts = (app_data.get("submission") or {}).get("attempts", 0) + 1

    try:
        outcome: SubmissionOutcome = await adapter.submit(
            job_url=task.job_url,
            profile=user_data,
            application=app_data,
            posting=posting,
            uid=task.uid,
            app_id=task.app_id,
        )
    except Exception as exc:
        log.exception("adapter crashed app=%s", task.app_id)
        if attempts >= MAX_ATTEMPTS:
            _fail(task, attempts, f"crashed after {attempts} attempts: {exc}")
            return {"status": "failed"}
        _requeue(task, attempts, str(exc))
        raise HTTPException(status_code=500, detail="retry")  # Cloud Tasks retries

    if outcome.success:
        advance(db, task.uid, task.app_id, AppState.SUBMITTING, AppState.SUBMITTED,
                note="confirmed by adapter",
                extra_fields={"submission": {
                    "tier": outcome.tier,
                    "attempts": attempts,
                    "screenshots": outcome.screenshots,
                    "confirmedAt": datetime.now(timezone.utc),
                    "error": None,
                }})
        return {"status": "submitted"}

    if outcome.escalate or attempts >= MAX_ATTEMPTS:
        _escalate(task, outcome.reason or "adapter escalated", attempts, outcome.screenshots)
        return {"status": "needs_human"}

    _requeue(task, attempts, outcome.reason or "retryable failure")
    raise HTTPException(status_code=500, detail="retry")


def _escalate(task: SubmitTask, reason: str, attempts: int = 0,
              screenshots: list[str] | None = None) -> None:
    assert db is not None
    advance(db, task.uid, task.app_id, AppState.SUBMITTING, AppState.NEEDS_HUMAN,
            note=reason,
            extra_fields={"submission": {
                "tier": 3, "attempts": attempts,
                "screenshots": screenshots or [], "error": reason,
            }})


def _fail(task: SubmitTask, attempts: int, reason: str) -> None:
    assert db is not None
    advance(db, task.uid, task.app_id, AppState.SUBMITTING, AppState.FAILED,
            note=reason,
            extra_fields={"submission.attempts": attempts, "submission.error": reason})


def _requeue(task: SubmitTask, attempts: int, reason: str) -> None:
    """Roll SUBMITTING back to QUEUED so the retried delivery passes the gate."""
    assert db is not None
    ref = (
        db.collection("users").document(task.uid)
        .collection("applications").document(task.app_id)
    )
    ref.update({
        "state": AppState.QUEUED.value,
        "submission.attempts": attempts,
        "submission.error": reason,
        "updatedAt": datetime.now(timezone.utc),
    })
