"""Drafting pipeline: matched application -> letter + screening answers.

Flow: draft (conditioned on the user's real writing samples) -> critique
(fact-grounding + generic-phrase check) -> one revision if needed -> store and
advance to IN_REVIEW. The critique step is what keeps letters from claiming
experience the user doesn't have.
"""
from __future__ import annotations

import logging

from google.cloud import firestore
from pydantic import BaseModel, Field

from llm import generate, generate_structured
from schemas import AppState, Letter, ScreeningAnswers, UserProfile
from state_machine import advance

log = logging.getLogger("drafting")

DRAFT_SYSTEM = """You write cover letters that sound like the candidate, not \
like an AI. You are given the candidate's real writing samples: match their \
sentence rhythm, vocabulary level, and directness. Hard rules: never claim \
experience not present in the work history; no "I am excited to apply"; no \
"passionate"; no restating the job description back at the company; 250-350 \
words; specific over general in every sentence."""

CRITIQUE_SYSTEM = """You are a skeptical hiring manager reviewing a cover \
letter against the candidate's actual work history. Flag: (1) any claim not \
supported by the history — quote it; (2) generic AI-sounding phrases; (3) \
factual mismatches with the job posting. If the letter is clean, say so."""


class Critique(BaseModel):
    passed: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    generic_phrases: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def draft_application(db: firestore.Client, uid: str, app_id: str) -> None:
    user_snap = db.collection("users").document(uid).get()
    app_snap = (
        db.collection("users").document(uid)
        .collection("applications").document(app_id).get()
    )
    if not (user_snap.exists and app_snap.exists):
        return

    profile = UserProfile.model_validate(user_snap.to_dict())
    app_data = app_snap.to_dict()
    posting = db.collection("jobPostings").document(app_data["posting_id"]).get().to_dict()
    if posting is None:
        return

    # --- draft ---
    letter_text = generate(
        _draft_prompt(profile, posting, app_data),
        system=DRAFT_SYSTEM,
        max_tokens=1200,
        temperature=0.8,
    ).strip()

    # --- critique + one revision ---
    critique = generate_structured(
        _critique_prompt(profile, posting, letter_text),
        Critique,
        system=CRITIQUE_SYSTEM,
    )
    version = 1
    if not critique.passed:
        letter_text = generate(
            _revision_prompt(letter_text, critique),
            system=DRAFT_SYSTEM,
            max_tokens=1200,
            temperature=0.5,
        ).strip()
        critique = generate_structured(
            _critique_prompt(profile, posting, letter_text),
            Critique,
            system=CRITIQUE_SYSTEM,
        )
        version = 2

    # --- screening answers ---
    answers = generate_structured(
        _answers_prompt(profile, posting),
        ScreeningAnswers,
        system="Write short, plain, first-person answers a candidate would "
               "give on an application form. Never invent facts.",
    )

    letter = Letter(
        text=letter_text,
        version=version,
        critique_passed=critique.passed,
        critique_notes=critique.notes + critique.unsupported_claims,
    )

    # matched -> drafted -> in_review (two hops; drafted is momentary here but
    # kept distinct so a future async pipeline can pause between them)
    if advance(db, uid, app_id, AppState.MATCHED, AppState.DRAFTED,
               note=f"letter v{version}, critique_passed={critique.passed}",
               extra_fields={
                   "letter": letter.model_dump(mode="json"),
                   "screeningAnswers": answers.model_dump(mode="json"),
               }):
        advance(db, uid, app_id, AppState.DRAFTED, AppState.IN_REVIEW)
        log.info("uid=%s app=%s drafted (v%d)", uid, app_id, version)


def _draft_prompt(profile: UserProfile, posting: dict, app_data: dict) -> str:
    samples = "\n\n---\n\n".join(
        f"[{s.title}]\n{s.text[:2000]}" for s in profile.writing_samples[:3]
    ) or "(no samples provided — use a plain, direct, professional voice)"
    history = "\n".join(
        f"- {w.title} at {w.company} ({w.start} to {w.end or 'present'}): "
        + "; ".join(w.bullets)
        for w in profile.work_history
    )
    reasons = "; ".join((app_data.get("match") or {}).get("reasons", []))
    return f"""CANDIDATE WRITING SAMPLES (match this voice):
{samples}

WORK HISTORY (the only facts you may use):
{history}

Skills: {", ".join(profile.skills)}

JOB: {posting.get("title")} at {posting.get("company")}
{(posting.get("descriptionText") or "")[:5000]}

Why this is a fit (from matching): {reasons}

Write the cover letter body only — no address block, no date, no
"Dear Hiring Manager" salutation, no signature."""


def _critique_prompt(profile: UserProfile, posting: dict, letter: str) -> str:
    history = "\n".join(
        f"- {w.title} at {w.company}: " + "; ".join(w.bullets)
        for w in profile.work_history
    )
    return f"""WORK HISTORY (ground truth):
{history}

JOB: {posting.get("title")} at {posting.get("company")}

LETTER TO REVIEW:
{letter}"""


def _revision_prompt(letter: str, critique: Critique) -> str:
    issues = "\n".join(
        f"- {c}" for c in critique.unsupported_claims + critique.generic_phrases + critique.notes
    )
    return f"""Revise this letter to fix every issue below. Keep everything
that wasn't flagged. Output the revised letter body only.

ISSUES:
{issues}

LETTER:
{letter}"""


def _answers_prompt(profile: UserProfile, posting: dict) -> str:
    return f"""Candidate: {profile.name}, {profile.location or "location unspecified"}.
Work authorization: {profile.work_auth or "not stated — leave work_auth null"}.
Salary target: {profile.salary_target or "not stated — leave salary null"}.
Skills: {", ".join(profile.skills)}.

Job: {posting.get("title")} at {posting.get("company")}.
Description excerpt: {(posting.get("descriptionText") or "")[:2500]}

Fill the screening answers: why_company (2-3 sentences, specific to this
company), salary (restate the target verbatim if provided), work_auth
(restate verbatim if provided)."""
