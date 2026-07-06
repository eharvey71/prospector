"""Firebase Functions (2nd gen, Python) entrypoint.

Wiring only — the logic lives in discovery.py, matching.py, drafting.py.

Triggers:
  crawl_boards            Cloud Scheduler, every 6h -> discovery
  on_posting_written      jobPostings/{id} written  -> fan out matching
  on_application_written  applications/{id} written -> route by state:
                            matched  -> drafting
                            approved -> enqueue Cloud Task to the worker
  on_resume_uploaded      Storage finalize on users/{uid}/resume.pdf ->
                            LLM extraction staged for review in profile UI
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Deployed env vars win (load_dotenv never overrides existing vars); this
# makes .env visible to bare imports and CLI function discovery alike.
load_dotenv(Path(__file__).parent / ".env")

from firebase_admin import initialize_app
from firebase_functions import firestore_fn, https_fn, options, scheduler_fn, storage_fn
from google.cloud import firestore, tasks_v2

from schemas import AppState, AtsType, SubmitTask
from state_machine import advance

initialize_app()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

options.set_global_options(region="us-central1", memory=options.MemoryOption.MB_512)

# Worker / queue config — set via `firebase functions:config` or env in deploy.
PROJECT = os.environ.get("GCLOUD_PROJECT", "")
QUEUE_LOCATION = os.environ.get("TASKS_LOCATION", "us-central1")
QUEUE_NAME = os.environ.get("TASKS_QUEUE", "submissions")
WORKER_URL = os.environ.get("WORKER_URL", "")  # Cloud Run URL, set after deploy
WORKER_SA = os.environ.get("WORKER_INVOKER_SA", "")  # service account email
# Default bucket for resume uploads — same value the web app uses
# (NEXT_PUBLIC_FB_STORAGE_BUCKET). Set in .env; when unset, falls back to the
# SDK resolving it from FIREBASE_CONFIG, which is absent in local imports and
# has broken function discovery at deploy time before. Set it explicitly.
STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET") or None
# Eventarc requires a Storage trigger to be in the same region as its bucket.
# This project's default bucket lives in us-east1, unlike everything else,
# so the resume trigger alone overrides the us-central1 global default.
STORAGE_REGION = os.environ.get("STORAGE_REGION", "us-central1")


def _db() -> firestore.Client:
    return firestore.Client()


# ---------------------------------------------------------------------------
# Discovery (scheduled)
# ---------------------------------------------------------------------------

@scheduler_fn.on_schedule(schedule="every 6 hours", timeout_sec=540)
def crawl_boards(event: scheduler_fn.ScheduledEvent) -> None:
    from discovery import run_discovery
    run_discovery(_db())


# ---------------------------------------------------------------------------
# Matching (posting written -> score for every user)
# ---------------------------------------------------------------------------

@firestore_fn.on_document_written(
    document="jobPostings/{postingId}", timeout_sec=300,
    secrets=["ANTHROPIC_API_KEY"],
)
def on_posting_written(event: firestore_fn.Event) -> None:
    if event.data is None or event.data.after is None:
        return
    posting = event.data.after.to_dict()
    if not posting or not posting.get("active"):
        return

    # Skip if the doc content didn't meaningfully change (lastSeen-only bumps).
    before = event.data.before.to_dict() if event.data.before else None
    if before and before.get("descriptionText") == posting.get("descriptionText"):
        return

    from matching import match_posting_for_user
    db = _db()
    posting_id = event.params["postingId"]
    for user_doc in db.collection("users").stream():
        try:
            match_posting_for_user(db, user_doc.id, posting_id, posting)
        except Exception:
            log.exception("matching failed uid=%s posting=%s", user_doc.id, posting_id)


# ---------------------------------------------------------------------------
# Company suggester (callable from the profile UI). LLM proposes, the board
# APIs verify; the client decides what joins the watchlist.
# ---------------------------------------------------------------------------

@https_fn.on_call(timeout_sec=300, secrets=["ANTHROPIC_API_KEY"])
def suggest_companies(req: https_fn.CallableRequest) -> dict:
    if req.auth is None:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.UNAUTHENTICATED, "sign in first")
    role = (req.data or {}).get("role", "").strip()
    if not role:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.INVALID_ARGUMENT, "role is required")

    db = _db()
    # Companies already on the watchlist aren't suggested again.
    wl = (
        db.collection("users").document(req.auth.uid)
        .collection("watchlist").document("companies").get().to_dict() or {}
    )
    exclude = {s.lower() for s in wl.get("greenhouse", []) + wl.get("lever", [])}
    profile = db.collection("users").document(req.auth.uid).get().to_dict() or {}

    from suggest import suggest_companies as run_suggest
    return {"companies": run_suggest(
        role, exclude,
        location=profile.get("location") or "",
        remote_only=bool((profile.get("preferences") or {}).get("remote_only")),
    )}


# ---------------------------------------------------------------------------
# User-added job by URL: the front door for ATSes the crawler doesn't know.
# Creates a source=unknown posting (Tier 2 submission) and matches it for
# the requesting user with the score gate bypassed — they chose it.
# ---------------------------------------------------------------------------

@https_fn.on_call(timeout_sec=300, secrets=["ANTHROPIC_API_KEY"])
def add_job_url(req: https_fn.CallableRequest) -> dict:
    if req.auth is None:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.UNAUTHENTICATED, "sign in first")
    url = (req.data or {}).get("url", "").strip()
    if not url.startswith("http"):
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.INVALID_ARGUMENT, "url is required")

    from matching import match_posting_for_user
    from urljob import create_posting_from_url
    db = _db()
    try:
        posting_id, posting = create_posting_from_url(db, url)
    except ValueError as exc:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.FAILED_PRECONDITION, str(exc))

    match_posting_for_user(db, req.auth.uid, posting_id, posting, force=True)
    return {"posting_id": posting_id,
            "company": posting.get("company"),
            "title": posting.get("title")}


# ---------------------------------------------------------------------------
# Resume upload -> profile extraction (staged for human review, never applied
# directly; the profile UI owns the merge)
# ---------------------------------------------------------------------------

@storage_fn.on_object_finalized(bucket=STORAGE_BUCKET, region=STORAGE_REGION,
                                timeout_sec=300, secrets=["ANTHROPIC_API_KEY"])
def on_resume_uploaded(event: storage_fn.CloudEvent[storage_fn.StorageObjectData]) -> None:
    from resume import process_resume_upload
    # Fires for every object in the default bucket; resume.py filters to
    # users/{uid}/resume.pdf and ignores everything else (e.g. screenshots).
    process_resume_upload(_db(), event.data.bucket, event.data.name)


# ---------------------------------------------------------------------------
# Application state router
# ---------------------------------------------------------------------------

@firestore_fn.on_document_written(
    document="users/{uid}/applications/{appId}", timeout_sec=540,
    secrets=["ANTHROPIC_API_KEY"],
)
def on_application_written(event: firestore_fn.Event) -> None:
    if event.data is None or event.data.after is None:
        return
    after = event.data.after.to_dict()
    if not after:
        return
    before = event.data.before.to_dict() if event.data.before else None
    if before and before.get("state") == after.get("state"):
        return  # not a state change (e.g. letter edit)

    uid = event.params["uid"]
    app_id = event.params["appId"]
    state = AppState(after["state"])

    if state == AppState.MATCHED:
        from drafting import draft_application
        try:
            draft_application(_db(), uid, app_id)
        except Exception:
            log.exception("drafting failed uid=%s app=%s", uid, app_id)
            advance(_db(), uid, app_id, AppState.MATCHED, AppState.FAILED,
                    note="drafting error; see logs")

    elif state == AppState.APPROVED:
        _enqueue_submission(uid, app_id, after)


def _enqueue_submission(uid: str, app_id: str, app_data: dict) -> None:
    db = _db()
    posting = db.collection("jobPostings").document(app_data["posting_id"]).get().to_dict()
    if posting is None:
        return
    if not WORKER_URL:
        log.warning("WORKER_URL not set; leaving app %s in approved", app_id)
        return

    payload = SubmitTask(
        uid=uid,
        app_id=app_id,
        posting_id=app_data["posting_id"],
        ats_type=AtsType(posting.get("source", "unknown")),
        job_url=str(posting.get("url", "")),
    )
    
    if not advance(db, uid, app_id, AppState.APPROVED, AppState.QUEUED,
               note="enqueuing cloud task"):
        return  # another invocation already handled this state change

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(PROJECT, QUEUE_LOCATION, QUEUE_NAME)
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{WORKER_URL}/submit",
            "headers": {"Content-Type": "application/json"},
            "body": payload.model_dump_json().encode(),
            "oidc_token": {"service_account_email": WORKER_SA},
        }
    }
    client.create_task(request={"parent": parent, "task": task})
    log.info("enqueued submission task uid=%s app=%s", uid, app_id)
    