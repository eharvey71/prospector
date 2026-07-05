"""Shared data contracts for the job engine.

Both /functions and /worker install this package (see their requirements.txt
referencing ../shared). Firestore documents are validated against these models
at every boundary so a bad write fails loudly instead of corrupting state.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class AppState(str, Enum):
    DISCOVERED = "discovered"      # posting matched to user, application created
    MATCHED = "matched"            # scored above threshold, awaiting drafting
    DRAFTED = "drafted"            # letter + answers generated, critique passed
    IN_REVIEW = "in_review"        # surfaced to the user in the UI
    APPROVED = "approved"          # human approved -> enqueue submission
    REJECTED = "rejected"          # human declined; terminal
    QUEUED = "queued"              # Cloud Task created
    SUBMITTING = "submitting"      # worker actively driving the browser
    SUBMITTED = "submitted"        # confirmed; terminal (until feedback loop)
    FAILED = "failed"              # exhausted retries; terminal
    NEEDS_HUMAN = "needs_human"    # escalated with pre-filled answers


# Engine-legal transitions. Client-legal transitions are enforced separately
# in firestore.rules; this map is what the trigger functions check before
# acting, so a replayed/duplicate event can never double-fire a stage.
ENGINE_TRANSITIONS: dict[AppState, set[AppState]] = {
    AppState.DISCOVERED: {AppState.MATCHED, AppState.REJECTED},
    AppState.MATCHED: {AppState.DRAFTED, AppState.FAILED},
    AppState.DRAFTED: {AppState.IN_REVIEW},
    AppState.APPROVED: {AppState.QUEUED},
    AppState.QUEUED: {AppState.SUBMITTING},
    AppState.SUBMITTING: {AppState.SUBMITTED, AppState.FAILED, AppState.NEEDS_HUMAN},
}


def transition_allowed(before: AppState, after: AppState) -> bool:
    return after in ENGINE_TRANSITIONS.get(before, set())


# ---------------------------------------------------------------------------
# ATS taxonomy
# ---------------------------------------------------------------------------

class AtsType(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

class WorkHistoryItem(BaseModel):
    company: str
    title: str
    start: str                      # "2021-03" — keep as string, months suffice
    end: Optional[str] = None       # None = current
    bullets: list[str] = Field(default_factory=list)


class WritingSample(BaseModel):
    title: str
    text: str


class Preferences(BaseModel):
    titles: list[str] = Field(default_factory=list)
    remote_only: bool = False
    exclude_companies: list[str] = Field(default_factory=list)
    min_match_score: int = 70       # 0-100 gate before drafting


class UserProfile(BaseModel):
    name: str
    email: str
    location: Optional[str] = None
    work_auth: Optional[str] = None
    salary_target: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    work_history: list[WorkHistoryItem] = Field(default_factory=list)
    writing_samples: list[WritingSample] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)


# ---------------------------------------------------------------------------
# Job postings
# ---------------------------------------------------------------------------

class JobPosting(BaseModel):
    source: AtsType
    external_id: str                # ID within the source system
    company: str
    title: str
    url: HttpUrl
    location: Optional[str] = None
    description_text: str = ""
    parsed_requirements: list[str] = Field(default_factory=list)
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    active: bool = True

    @property
    def posting_id(self) -> str:
        """Deterministic doc ID -> writes are natural upserts, dedup for free."""
        raw = f"{self.source.value}:{self.company.lower()}:{self.external_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

class MatchResult(BaseModel):
    score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


class Letter(BaseModel):
    text: str
    version: int = 1
    critique_passed: bool = False
    critique_notes: list[str] = Field(default_factory=list)


class ScreeningAnswers(BaseModel):
    why_company: Optional[str] = None
    salary: Optional[str] = None
    work_auth: Optional[str] = None
    extra: dict[str, str] = Field(default_factory=dict)


class SubmissionRecord(BaseModel):
    tier: Optional[int] = None      # 1 deterministic, 2 agentic, 3 human
    attempts: int = 0
    screenshots: list[str] = Field(default_factory=list)  # Storage paths
    confirmed_at: Optional[datetime] = None
    error: Optional[str] = None


class StateEvent(BaseModel):
    state: AppState
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: Optional[str] = None


class Application(BaseModel):
    posting_id: str
    state: AppState = AppState.DISCOVERED
    state_history: list[StateEvent] = Field(default_factory=list)
    match: Optional[MatchResult] = None
    letter: Optional[Letter] = None
    screening_answers: Optional[ScreeningAnswers] = None
    submission: SubmissionRecord = Field(default_factory=SubmissionRecord)
    review_note: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Cloud Task payload: functions -> worker
# ---------------------------------------------------------------------------

class SubmitTask(BaseModel):
    uid: str
    app_id: str
    posting_id: str
    ats_type: AtsType
    job_url: str
