"""Matching: posting x profile -> score, reasons, red flags.

Cheap prefilter (title/skill keyword overlap) gates the LLM call so a big
crawl doesn't turn into a big bill. Everything above the user's
min_match_score becomes an application in MATCHED state, which triggers
drafting.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore

from llm import generate_structured
from schemas import AppState, Application, MatchResult, StateEvent, UserProfile

log = logging.getLogger("matching")

MATCH_SYSTEM = """You are a rigorous recruiting analyst. Score how well a \
candidate fits a job posting on 0-100. Be conservative: 85+ means the \
candidate could credibly be a top-3 applicant. Never invent experience.

Write for a candidate deciding in ten seconds whether to apply. summary is \
your out-loud verdict in one sentence. reasons tie the candidate's actual \
history to the job's stated needs, one short sentence each. red_flags are \
capped at five, most serious first, one gap per flag stated once — severity \
"blocker" is reserved for a hard requirement in the posting the candidate \
clearly fails; everything else is a "concern"."""

# Early-career candidates would score near zero against the veteran
# yardstick — recalibrate what the numbers MEAN, not just grade softer.
ENTRY_MATCH_SYSTEM = """You are a recruiting analyst evaluating an \
EARLY-CAREER candidate (new to the job market) against a job posting. Score \
fit 0-100 where 85+ means "a strong entry-level applicant for this role": \
weigh education, coursework, projects, internships, and transferable skills \
the way you would weigh work history for a veteran. Potential and \
demonstrated learning count; years of experience do not, unless the posting \
hard-requires them. Never invent experience.

Write for a candidate deciding in ten seconds whether to apply. summary is \
your out-loud verdict in one sentence. reasons tie the candidate's actual \
education/projects/skills to the job's stated needs. red_flags are capped \
at five — severity "blocker" ONLY for an explicit hard requirement the \
candidate clearly fails (e.g. "5+ years required", a degree they lack, a \
clearance); a general preference for experience is a "concern", not a \
blocker."""

# Titles an entry-stage user can't land — skip before spending LLM tokens.
SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|staff|principal|director|vp|vice president|head of|chief)\b",
    re.I)


def _bump(db: firestore.Client, uid: str, **fields: int) -> None:
    """Increment per-user funnel counters. Best-effort: stats must never
    break matching."""
    try:
        doc = {k: firestore.Increment(v) for k, v in fields.items()}
        doc["updatedAt"] = datetime.now(timezone.utc)
        (db.collection("users").document(uid)
         .collection("stats").document("funnel").set(doc, merge=True))
    except Exception:
        log.warning("funnel bump failed uid=%s", uid, exc_info=True)


def match_posting_for_user(
    db: firestore.Client,
    uid: str,
    posting_id: str,
    posting: dict,
    force: bool = False,
) -> None:
    """force=True (user added this job explicitly): skip the prefilter and
    the score gate — the job always proceeds to drafting, score recorded
    for the review UI."""
    user_snap = db.collection("users").document(uid).get()
    if not user_snap.exists:
        return
    profile = UserProfile.model_validate(user_snap.to_dict())

    app_id = posting_id  # one application per posting per user; natural dedup
    app_ref = (
        db.collection("users").document(uid)
        .collection("applications").document(app_id)
    )
    snap = app_ref.get()
    if snap.exists:
        # Already evaluated (idempotency on replayed triggers). A forced
        # re-add is the user saying "I want this one": revive it if a
        # previous run rejected or failed it.
        if force:
            _revive_if_terminal(app_ref, snap.to_dict() or {})
        return

    _bump(db, uid, seen=1)
    if not force and not _prefilter(profile, posting):
        _bump(db, uid, prefiltered=1)
        return

    result = generate_structured(
        _match_prompt(profile, posting),
        MatchResult,
        system=ENTRY_MATCH_SYSTEM if profile.career_stage == "entry" else MATCH_SYSTEM,
        max_tokens=1000,
    )

    threshold = profile.preferences.min_match_score
    passed = force or result.score >= threshold
    state = AppState.MATCHED if passed else AppState.REJECTED
    note = f"score {result.score} vs threshold {threshold}" + (
        " (user-added, gate bypassed)" if force else "")

    app = Application(
        posting_id=posting_id,
        user_added=force,
        state=state,
        state_history=[
            StateEvent(state=AppState.DISCOVERED, note="created by matcher"),
            StateEvent(state=state, note=note),
        ],
        match=result,
    )
    try:
        # create(), not set(): add_job_url's forced match and the
        # on_posting_written fan-out both score the same posting for the
        # same user concurrently — the slower write must lose, not clobber
        # (a non-forced REJECTED once overwrote a user-added MATCHED here).
        app_ref.create(app.model_dump(mode="json"))
    except AlreadyExists:
        log.info("uid=%s posting=%s lost creation race", uid, posting_id)
        if force:
            _revive_if_terminal(app_ref, app_ref.get().to_dict() or {})
        return
    _bump(db, uid, scored=1, **({"matched": 1} if passed else {}))
    log.info("uid=%s posting=%s -> %s (%s)", uid, posting_id, state.value, note)


def _revive_if_terminal(app_ref, current: dict) -> None:
    """User re-added a job whose application is dead (rejected/failed):
    flip it back to MATCHED with the gate-bypass flag so drafting reruns.
    Applications still in flight are left untouched."""
    state = current.get("state")
    if state not in (AppState.REJECTED.value, AppState.FAILED.value):
        return
    now = datetime.now(timezone.utc)
    app_ref.update({
        "state": AppState.MATCHED.value,
        "user_added": True,
        "updatedAt": now,
        "stateHistory": firestore.ArrayUnion([{
            "state": AppState.MATCHED.value, "ts": now,
            "note": f"revived from {state} — user re-added by URL",
        }]),
    })
    log.info("revived %s from %s -> matched", app_ref.id, state)


def _prefilter(profile: UserProfile, posting: dict) -> bool:
    """Zero-cost gate before spending LLM tokens."""
    company = (posting.get("company") or "").lower()
    if company in {c.lower() for c in profile.preferences.exclude_companies}:
        return False

    # Entry-stage users can't land senior+ roles; don't score them.
    if profile.career_stage == "entry" and SENIOR_TITLE.search(posting.get("title") or ""):
        return False

    title = (posting.get("title") or "").lower()
    # Titles plus their LLM-expanded synonyms — "grant writer" also matches
    # "Development Director" postings (see synonyms.py).
    wanted = [t.lower() for t in (profile.preferences.titles
                                  + profile.preferences.title_synonyms)]
    if wanted and not any(w in title for w in wanted):
        # Fall back to skill overlap in the description
        desc = (posting.get("descriptionText") or posting.get("description_text") or "").lower()
        hits = sum(1 for s in profile.skills if s.lower() in desc)
        if hits < 3:
            return False
    return True


def _match_prompt(profile: UserProfile, posting: dict) -> str:
    history = "\n".join(
        f"- {w.title} at {w.company} ({w.start} to {w.end or 'present'}): "
        + "; ".join(w.bullets[:4])
        for w in profile.work_history
    )
    education = "\n".join(
        f"- {e.degree}, {e.school}" + (f" ({e.year})" if e.year else "")
        + (": " + "; ".join(e.bullets[:4]) if e.bullets else "")
        for e in profile.education
    )
    projects = "\n".join(
        f"- {p.name} [{', '.join(p.tech)}]: {p.description[:300]}"
        for p in profile.projects
    )
    desc = (posting.get("descriptionText") or posting.get("description_text") or "")[:6000]
    return f"""CANDIDATE (career stage: {profile.career_stage})
Skills: {", ".join(profile.skills)}
Location: {profile.location or "unspecified"} | Remote only: {profile.preferences.remote_only}
Work history:
{history or "(none)"}
Education:
{education or "(none listed)"}
Projects:
{projects or "(none listed)"}

JOB POSTING
Company: {posting.get("company")}
Title: {posting.get("title")}
Location: {posting.get("location") or "unspecified"}
Description:
{desc}

Score this fit."""
