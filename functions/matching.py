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

from pydantic import BaseModel, Field

from llm import generate_structured
from schemas import (AppState, Application, MatchResult, MatchRubric, RedFlag,
                     StateEvent, UserProfile)

log = logging.getLogger("matching")

MATCH_SYSTEM = """You are a rigorous recruiting analyst. Rate how well a \
candidate fits a job posting on FOUR separate 0-10 dimensions (skills, \
seniority, domain, logistics) — the final score is computed from your \
ratings, not by you. Rate each dimension on evidence alone; 10 means ideal, \
5 means genuinely mixed, 0 means no fit. Use the full range and never \
invent experience.

summary is your out-loud one-sentence verdict for the candidate. red_flags \
are capped at five, most serious first, one gap per flag stated once — \
severity "blocker" is reserved for a hard requirement in the posting the \
candidate clearly fails; everything else is a "concern"."""

# Early-career candidates would score near zero against the veteran
# yardstick — recalibrate what the ratings MEAN, not just grade softer.
ENTRY_MATCH_SYSTEM = """You are a recruiting analyst evaluating an \
EARLY-CAREER candidate (new to the job market) against a job posting. Rate \
fit on FOUR separate 0-10 dimensions (skills, seniority, domain, \
logistics) — the final score is computed from your ratings, not by you. \
Weigh education, coursework, projects, internships, and transferable \
skills the way you would weigh work history for a veteran: skills = what \
they can demonstrably do; seniority = 10 when the ROLE is entry-level \
appropriate, low only when the role demands years they lack; domain = \
sector familiarity from any source. Use the full 0-10 range; never invent \
experience.

summary is your out-loud one-sentence verdict. red_flags capped at five — \
severity "blocker" ONLY for an explicit hard requirement the candidate \
clearly fails ("5+ years required", a degree they lack, a clearance); a \
general preference for experience is a "concern", not a blocker."""

# Weighted rubric -> 0-100 (integer weights summing to 100: no float fuzz).
# Domain weighs heavy on purpose: it's what keeps same-title-different-world
# matches (edtech vs grant writing) apart.
RUBRIC_WEIGHTS = {"skills": 35, "seniority": 25, "domain": 30,
                  "logistics": 10}


class MatchAssessment(BaseModel):
    """What the LLM emits; code turns it into a MatchResult."""
    summary: str = ""
    rubric: MatchRubric
    red_flags: list[RedFlag] = Field(default_factory=list, max_length=5)
    posting_salary: str | None = Field(
        default=None,
        description="salary/compensation range STATED in the posting, "
                    "copied verbatim; null when the posting doesn't state one")


def _to_match_result(a: MatchAssessment) -> MatchResult:
    dims = {d: getattr(a.rubric, d) for d in RUBRIC_WEIGHTS}
    score = round(sum(RUBRIC_WEIGHTS[d] * r.rating for d, r in dims.items()) / 10)
    reasons = [f"{d.capitalize()} {r.rating}/10 — {r.why}".rstrip(" —")
               for d, r in dims.items()]
    return MatchResult(
        score=score,
        rubric={d: r.rating for d, r in dims.items()},
        summary=a.summary,
        reasons=reasons,
        red_flags=a.red_flags[:5],
        posting_salary=(a.posting_salary or "").strip()[:80] or None,
    )

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

    # Source scoping: users are matched only against boards THEY watch.
    # Postings are a global pool (crawled from the union of all watchlists),
    # and without this check every user got scored against every other
    # user's boards — wrong hits, wasted LLM spend.
    if not force and not _board_watched(db, uid, posting):
        return

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

    assessment = generate_structured(
        _match_prompt(profile, posting),
        MatchAssessment,
        system=ENTRY_MATCH_SYSTEM if profile.career_stage == "entry" else MATCH_SYSTEM,
        max_tokens=1200,
    )
    result = _to_match_result(assessment)

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
    doc = app.model_dump(mode="json")
    # Every list query orders by a field; Firestore EXCLUDES docs missing
    # it. Without updatedAt at creation, score-gate rejections never
    # appeared in the Done tab (transitions set it, creation didn't).
    doc["updatedAt"] = datetime.now(timezone.utc)
    try:
        # create(), not set(): add_job_url's forced match and the
        # on_posting_written fan-out both score the same posting for the
        # same user concurrently — the slower write must lose, not clobber
        # (a non-forced REJECTED once overwrote a user-added MATCHED here).
        app_ref.create(doc)
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


def _board_watched(db: firestore.Client, uid: str, posting: dict) -> bool:
    """Is this posting's board on the user's own watchlist? Postings with
    source=unknown (pasted URLs, career-page crawls) can't be attributed to
    a board and stay visible to everyone."""
    src = posting.get("source", "unknown")
    if src == "unknown":
        return True
    wl = (db.collection("users").document(uid)
          .collection("watchlist").document("companies").get().to_dict() or {})
    comp = (posting.get("company") or "").lower()
    if src == "workday":
        # watchlist stores full myworkdayjobs URLs; company is the tenant
        return any(comp and comp in (u or "").lower()
                   for u in wl.get("workday", []))
    return comp in {(s or "").lower() for s in wl.get(src, [])}


US_STATE_NAMES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut",
    "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii",
    "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa",
    "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine",
    "md": "maryland", "ma": "massachusetts", "mi": "michigan",
    "mn": "minnesota", "ms": "mississippi", "mo": "missouri",
    "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico",
    "ny": "new york", "nc": "north carolina", "nd": "north dakota",
    "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota",
    "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}

REMOTE_RX = re.compile(r"\b(fully )?remote\b|\bwork from home\b|\bwfh\b", re.I)


def _work_mode(prefs) -> str:
    """The stored work_mode, or its meaning derived from the legacy
    checkbox pair for docs saved before it existed."""
    mode = getattr(prefs, "work_mode", "") or ""
    if mode in ("local_or_remote", "local_only", "remote_only"):
        return mode
    if prefs.remote_only:
        return "remote_only"
    return "local_or_remote" if prefs.remote_ok else "local_only"


def _passes_location_gate(prefs, posting: dict) -> bool:
    """One gate for place + work arrangement, enforced pre-LLM.
    Deliberately generous on place: matching the CITY name in the
    posting's location field or the first stretch of the description
    passes — the LLM's logistics rating still grades precision. The gate
    exists to stop Seattle jobs reaching a Richmond-only user, not to
    adjudicate suburbs."""
    loc = (posting.get("location") or "").lower()
    desc = (posting.get("descriptionText")
            or posting.get("description_text") or "")[:2500].lower()
    remote = bool(REMOTE_RX.search(loc) or REMOTE_RX.search(desc))
    mode = _work_mode(prefs)
    if mode == "remote_only":
        return remote
    if not prefs.locations:
        return True
    local = _city_match(prefs.locations, loc, desc)
    return local or (remote and mode == "local_or_remote")


def _city_match(wanted: list[str], loc: str, desc: str) -> bool:
    for w in wanted:
        city = w.split(",")[0].strip().lower()
        if city and re.search(rf"\b{re.escape(city)}\b", loc + " " + desc):
            # Guard against same-name-different-state (Richmond CA vs VA):
            # when the user gave a state AND the posting names a different
            # one in its location field, reject the hit.
            parts = [p.strip().lower() for p in w.split(",")]
            want_state = parts[1] if len(parts) > 1 else ""
            if want_state and loc:
                want_full = US_STATE_NAMES.get(want_state, want_state)
                other = [f"{ab}|{nm}" for ab, nm in US_STATE_NAMES.items()
                         if ab != want_state and nm != want_full]
                if not re.search(rf"\b({want_state}|{want_full})\b", loc) \
                        and re.search(rf"\b({'|'.join(other)})\b", loc):
                    continue
            return True
    return False


def _prefilter(profile: UserProfile, posting: dict) -> bool:
    """Zero-cost gate before spending LLM tokens."""
    company = (posting.get("company") or "").lower()
    if company in {c.lower() for c in profile.preferences.exclude_companies}:
        return False

    # Place + work-arrangement gate: jobs outside the wanted places (or
    # non-remote jobs for a remote-only user) never reach the LLM or the
    # queue. No locations + a local mode = anywhere, as before.
    if not _passes_location_gate(profile.preferences, posting):
        return False

    # Entry-stage users can't land senior+ roles; don't score them.
    if profile.career_stage == "entry" and SENIOR_TITLE.search(posting.get("title") or ""):
        return False

    title = (posting.get("title") or "").lower()
    # Titles plus their LLM-expanded synonyms — "grant writer" also matches
    # "Development Director" postings (see synonyms.py).
    wanted = [t.lower() for t in (profile.preferences.titles
                                  + profile.preferences.title_synonyms)]
    title_hit = any(w in title for w in wanted)
    desc = (posting.get("descriptionText") or posting.get("description_text") or "").lower()
    # Skill vocabulary includes project tech — for early-career users the
    # projects often carry skills the flat list doesn't repeat.
    terms = {s.lower() for s in profile.skills}
    terms.update(t.lower() for p in profile.projects for t in p.tech)
    hits = sum(1 for s in terms if s and s in desc)

    if wanted and not title_hit and hits < 3:
        return False
    # Deterministic prescore: a title hit whose description shares ZERO
    # skill vocabulary is the cross-domain trap ("Director of X" in a world
    # the candidate has never touched) — don't spend an LLM call on it.
    # Thin descriptions are exempt: no text to find skills in.
    if title_hit and hits == 0 and len(desc) >= 500:
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
    mode_text = {
        "local_or_remote": "on-site there or fully remote",
        "local_only": "on-site there only — remote-only roles don't suit",
        "remote_only": "remote only",
    }[_work_mode(profile.preferences)]
    return f"""CANDIDATE (career stage: {profile.career_stage})
Skills: {", ".join(profile.skills)}
Location: {profile.location or "unspecified"}
Wants to work in: {", ".join(profile.preferences.locations) or "anywhere"} ({mode_text})
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
