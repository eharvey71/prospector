"""Firebase Functions (2nd gen, Python) entrypoint.

Wiring only — the logic lives in discovery.py, matching.py, drafting.py.

Triggers:
  crawl_boards            Cloud Scheduler, every 6h -> discovery
  on_posting_written      jobPostings/{id} written  -> fan out matching
  on_application_written  applications/{id} written -> route by state:
                            matched     -> drafting
                            approved    -> enqueue Cloud Task to the worker
                            needs_human -> suggest answers for escalated fields
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


def _health_error(kind: str, exc: Exception) -> None:
    """Count pipeline errors into the global health doc the UI surfaces.
    Best-effort — must never mask the original failure."""
    from datetime import datetime, timezone
    try:
        _db().collection("health").document("errors").set({
            kind: firestore.Increment(1),
            "lastErrorAt": datetime.now(timezone.utc),
            "lastError": f"{kind}: {exc}"[:300],
        }, merge=True)
    except Exception:
        log.debug("health error recording failed", exc_info=True)


# ---------------------------------------------------------------------------
# Discovery (scheduled)
# ---------------------------------------------------------------------------

@scheduler_fn.on_schedule(schedule="every 6 hours", timeout_sec=540,
                          secrets=["ANTHROPIC_API_KEY"])
def crawl_boards(event: scheduler_fn.ScheduledEvent) -> None:
    # Needs the LLM secret because custom career-page crawling classifies
    # links with generate_structured (see discovery._crawl_career_page).
    from datetime import datetime, timezone
    from discovery import run_discovery
    db = _db()
    ref = db.collection("health").document("crawl")
    try:
        n = run_discovery(db)
        ref.set({"lastRunAt": datetime.now(timezone.utc), "ok": True,
                 "postings": n, "error": None})
    except Exception as exc:
        ref.set({"lastRunAt": datetime.now(timezone.utc), "ok": False,
                 "error": str(exc)[:500]}, merge=True)
        raise


# ---------------------------------------------------------------------------
# Sweeper: applications stuck in SUBMITTING
# ---------------------------------------------------------------------------

@scheduler_fn.on_schedule(schedule="every 30 minutes", timeout_sec=120)
def sweep_stuck_submissions(event: scheduler_fn.ScheduledEvent) -> None:
    """Escalate applications abandoned mid-submission.

    If the worker container dies (OOM, timeout, redeploy) after QUEUED ->
    SUBMITTING, the redelivered Cloud Task hits the idempotency gate, gets
    acked as a duplicate, and the application would sit in SUBMITTING
    forever with nothing left to touch it. Anything in SUBMITTING for 30+
    minutes is dead — a live attempt finishes in a few minutes. Always
    escalates, never retries: the submitClickedAt marker says whether the
    Submit click happened, and when it has, resubmitting is forbidden."""
    from datetime import datetime, timedelta, timezone
    from google.cloud.firestore_v1.base_query import FieldFilter

    db = _db()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    stuck = (db.collection_group("applications")
             .where(filter=FieldFilter("state", "==", AppState.SUBMITTING.value))
             .where(filter=FieldFilter("updatedAt", "<", cutoff))
             .limit(50).stream())
    swept = 0
    for snap in stuck:
        uid = snap.reference.parent.parent.id
        data = snap.to_dict() or {}
        clicked = (data.get("submission") or {}).get("submitClickedAt")
        if clicked:
            reason = (f"submission attempt died after clicking Submit "
                      f"({clicked}) — check your email; do not reapply "
                      f"unless you're sure it never went through")
        else:
            reason = ("submission attempt died before clicking Submit — "
                      "the form was never filed; approve again to retry")
        try:
            if advance(db, uid, snap.id, AppState.SUBMITTING,
                       AppState.NEEDS_HUMAN, note=f"sweeper: {reason}",
                       extra_fields={"submission.error": reason}):
                swept += 1
                log.warning("swept stuck submission uid=%s app=%s clicked=%s",
                            uid, snap.id, bool(clicked))
        except Exception as exc:  # keep sweeping the rest
            _health_error("sweep", exc)
    if swept:
        log.info("sweeper escalated %d stuck submission(s)", swept)


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
        except Exception as exc:
            log.exception("matching failed uid=%s posting=%s", user_doc.id, posting_id)
            _health_error("matching", exc)


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
    exclude = {s.lower() for s in (wl.get("greenhouse", []) + wl.get("lever", [])
                                   + wl.get("workday", []))}
    profile = db.collection("users").document(req.auth.uid).get().to_dict() or {}
    prefs = profile.get("preferences") or {}

    from suggest import suggest_companies as run_suggest
    return run_suggest(   # {"companies": [...], "unverified": [...]}
        role, exclude,
        location=profile.get("location") or "",
        remote_only=bool(prefs.get("remote_only")),
        wanted_locations=prefs.get("locations") or [],
        work_mode=prefs.get("work_mode") or "local_or_remote",
        titles=prefs.get("titles") or [],
        skills=profile.get("skills") or [],
    )


@https_fn.on_call(timeout_sec=60, secrets=["ANTHROPIC_API_KEY"])
def expand_metro(req: https_fn.CallableRequest) -> dict:
    """Center + radius -> towns list for the Settings location field."""
    if req.auth is None:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.UNAUTHENTICATED, "sign in first")
    center = (req.data or {}).get("center", "").strip()
    if not center:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.INVALID_ARGUMENT, "center is required")
    radius = max(5, min(int((req.data or {}).get("radius") or 25), 100))
    from metro import expand_metro as run
    return {"towns": run(center, radius)}


# ---------------------------------------------------------------------------
# Track a company by name: resolve its ATS and register it automatically.
# ---------------------------------------------------------------------------

@https_fn.on_call(timeout_sec=120, secrets=["ANTHROPIC_API_KEY"])
def track_company(req: https_fn.CallableRequest) -> dict:
    if req.auth is None:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.UNAUTHENTICATED, "sign in first")
    name = (req.data or {}).get("name", "").strip()
    if not name:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.INVALID_ARGUMENT, "name is required")

    from track import track_company as run_track
    return run_track(_db(), req.auth.uid, name)


# ---------------------------------------------------------------------------
# Manual drafting: with auto_draft off, the UI requests each letter.
# ---------------------------------------------------------------------------

@https_fn.on_call(timeout_sec=540, secrets=["ANTHROPIC_API_KEY"])
def request_draft(req: https_fn.CallableRequest) -> dict:
    if req.auth is None:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.UNAUTHENTICATED, "sign in first")
    app_id = (req.data or {}).get("app_id", "").strip()
    if not app_id:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.INVALID_ARGUMENT, "app_id is required")

    db = _db()
    app_snap = (
        db.collection("users").document(req.auth.uid)
        .collection("applications").document(app_id).get()
    )
    if not app_snap.exists or app_snap.to_dict().get("state") != AppState.MATCHED.value:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.FAILED_PRECONDITION,
            "application not found or not awaiting drafting")

    from drafting import draft_application
    draft_application(db, req.auth.uid, app_id)
    return {"ok": True}


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

    # Admins may add a job straight into another user's queue (a parent
    # dropping a posting into a kid's pipeline). The claim is checked
    # server-side; a non-admin naming someone else is rejected.
    target_uid = ((req.data or {}).get("uid") or "").strip() or req.auth.uid
    if target_uid != req.auth.uid and not (req.auth.token or {}).get("admin"):
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.PERMISSION_DENIED,
            "only an admin can add a job for another user")

    from matching import match_posting_for_user
    from urljob import create_posting_from_url
    db = _db()
    try:
        posting_id, posting = create_posting_from_url(db, url)
    except ValueError as exc:
        raise https_fn.HttpsError(
            https_fn.FunctionsErrorCode.FAILED_PRECONDITION, str(exc))

    match_posting_for_user(db, target_uid, posting_id, posting, force=True)
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
# Profile writes -> title-synonym expansion (loop-guarded: the write-back
# doesn't change titles, so the retrigger no-ops)
# ---------------------------------------------------------------------------

@firestore_fn.on_document_written(
    document="users/{uid}", timeout_sec=120,
    secrets=["ANTHROPIC_API_KEY"],
)
def on_user_written(event: firestore_fn.Event) -> None:
    if event.data is None or event.data.after is None:
        return
    after = event.data.after.to_dict()
    if not after:
        return
    from synonyms import maybe_expand_titles
    try:
        maybe_expand_titles(_db(), event.params["uid"], after)
    except Exception:
        log.exception("title synonym expansion failed uid=%s", event.params["uid"])


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
        db = _db()
        # Manual-drafting mode: matches wait in the UI for a per-application
        # "write the letter" (request_draft). User-pasted jobs always draft.
        prefs = (db.collection("users").document(uid).get().to_dict()
                 or {}).get("preferences") or {}
        # auto_draft is about SPEND and applies to every match, including
        # ones the user added by URL. (user_added bypasses the score gate in
        # matching.py — that's a separate thing: the job is wanted
        # regardless of score. It must not also authorize LLM spend the
        # user switched off.)
        if not prefs.get("auto_draft", False):
            log.info("auto_draft off; app %s waits in matched", app_id)
            return
        from drafting import draft_application
        try:
            draft_application(db, uid, app_id)
        except Exception as exc:
            log.exception("drafting failed uid=%s app=%s", uid, app_id)
            _health_error("drafting", exc)
            advance(db, uid, app_id, AppState.MATCHED, AppState.FAILED,
                    note="drafting error; see logs")

    elif state == AppState.APPROVED:
        _enqueue_submission(uid, app_id, after)

    elif state == AppState.NEEDS_HUMAN:
        # Draft suggested answers for the exact questions the adapter
        # escalated, so finishing the form by hand is copy-paste. Best
        # effort — the card still works without suggestions.
        from drafting import suggest_escalation_answers
        try:
            suggest_escalation_answers(_db(), uid, app_id)
        except Exception:
            log.exception("escalation suggestions failed uid=%s app=%s", uid, app_id)


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
    