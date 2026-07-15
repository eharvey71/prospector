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
experience not present in the stated facts (work history, education, \
projects); no "I am excited to apply"; no "passionate"; no restating the job \
description back at the company; 250-350 words; specific over general in \
every sentence. For an early-career candidate, education and projects ARE \
the story — write them with the same concreteness a veteran's work history \
would get, and never apologize for a short history.

Two acceptance rules: (1) when the candidate genuinely has a skill or \
experience the posting names, use the POSTING'S exact terminology for it — \
recruiters and screening software search for their own words; never borrow \
their terminology for anything the candidate lacks. (2) if reviewer \
concerns are listed, address the most important one head-on in one or two \
confident sentences — reframe honestly, never apologize, never ignore it."""

CRITIQUE_SYSTEM = """You are a skeptical hiring manager reviewing a cover \
letter against the candidate's actual work history, education, and \
projects. Flag: (1) any claim not supported by those facts — quote it; (2) \
generic AI-sounding phrases; (3) factual mismatches with the job posting. \
If the letter is clean, say so."""


class Critique(BaseModel):
    passed: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    generic_phrases: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _facts_block(profile: UserProfile) -> str:
    """Work history + education + projects — the complete set of facts a
    letter or answer is allowed to claim."""
    history = "\n".join(
        f"- {w.title} at {w.company} ({w.start} to {w.end or 'present'}): "
        + "; ".join(w.bullets)
        for w in profile.work_history
    )
    education = "\n".join(
        f"- {e.degree}, {e.school}" + (f" ({e.year})" if e.year else "")
        + (": " + "; ".join(e.bullets) if e.bullets else "")
        for e in profile.education
    )
    projects = "\n".join(
        f"- {p.name} [{', '.join(p.tech)}]: {p.description}"
        for p in profile.projects
    )
    return (f"Work history:\n{history or '(none)'}\n"
            f"Education:\n{education or '(none listed)'}\n"
            f"Projects:\n{projects or '(none listed)'}")


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
               "give on an application form. Never invent facts. Skip any "
               "question whose answer isn't supported by the stated facts — "
               "an unanswered question is fine, a fabricated answer is not.",
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


class _EscalationSuggestion(BaseModel):
    field: str          # copied VERBATIM from the questions list
    suggestion: str


class _EscalationSuggestions(BaseModel):
    suggestions: list[_EscalationSuggestion] = Field(default_factory=list)


def suggest_escalation_answers(db: firestore.Client, uid: str, app_id: str) -> None:
    """When a submission escalates, draft suggested answers for the exact
    questions the adapter couldn't answer (the fill sheet's needs_you
    entries), from profile facts only. Suggestions are attached to the fill
    sheet for the human to copy — never filled into any form.

    Idempotent per escalation: submission.suggestions_done guards re-runs."""
    app_ref = (db.collection("users").document(uid)
               .collection("applications").document(app_id))
    app_data = app_ref.get().to_dict() or {}
    submission = app_data.get("submission") or {}
    sheet = submission.get("fill_sheet") or []
    needs = [e for e in sheet if e.get("status") != "filled"]
    if not needs or submission.get("suggestions_done"):
        return

    user = db.collection("users").document(uid).get().to_dict() or {}
    profile = UserProfile.model_validate(user)
    posting = (db.collection("jobPostings")
               .document(app_data.get("posting_id", "")).get().to_dict() or {})
    tri = (lambda v: "not stated" if v is None else ("Yes" if v else "No"))
    questions = "\n".join(f"- {e.get('field')}" for e in needs)

    result = generate_structured(
        f"""CANDIDATE FACTS (the only permitted sources):
Name: {profile.name}. Location: {profile.location or "not stated"}.
Work authorization: {profile.work_auth or "not stated"}.
Salary target: {profile.salary_target or "not stated"}\
 (for salary questions: {_salary_rule(profile)}).
Open to relocation: {tri(profile.screeners.open_to_relocation)}.
Willing to work in-person/onsite/hybrid: {tri(profile.screeners.onsite_ok)}.
Skills: {", ".join(profile.skills)}.
{_facts_block(profile)}

JOB: {posting.get("title")} at {posting.get("company")}

FORM QUESTIONS A HUMAN MUST ANSWER (from the application form):
{questions}

For each question the facts above can answer, produce a suggestion the
candidate can copy into the form (short; for yes/no dropdowns just
"Yes"/"No"). Copy the field text VERBATIM as the key. SKIP entirely:
consent or legal acknowledgments, captchas, demographic/EEO questions,
questions about interview or application history, and anything the facts
don't cover — no suggestion is better than a guess.""",
        _EscalationSuggestions,
        system="You help a candidate finish a job application form by hand. "
               "Suggest answers only from the stated facts.",
        max_tokens=1500,
    )

    by_field = {s.field.strip(): s.suggestion.strip()
                for s in result.suggestions if s.suggestion.strip()}
    for e in sheet:
        if e.get("status") != "filled" and e.get("field") in by_field:
            e["suggestion"] = by_field[e["field"]]

    app_ref.update({"submission.fill_sheet": sheet,
                    "submission.suggestions_done": True})
    log.info("uid=%s app=%s suggested answers for %d/%d escalated fields",
             uid, app_id, len(by_field), len(needs))


def _draft_prompt(profile: UserProfile, posting: dict, app_data: dict) -> str:
    samples = "\n\n---\n\n".join(
        f"[{s.title}]\n{s.text[:2000]}" for s in profile.writing_samples[:3]
    ) or "(no samples provided — use a plain, direct, professional voice)"
    match = app_data.get("match") or {}
    reasons = "; ".join(match.get("reasons", []))
    # Non-blocker red flags = the objections a reviewer will raise. The
    # letter is the one chance to rebut them (matching stores them as either
    # plain strings or {severity, topic, detail} dicts).
    concerns = []
    for fl in match.get("red_flags", []):
        if isinstance(fl, str):
            concerns.append(fl)
        elif fl.get("severity") != "blocker":
            concerns.append(": ".join(x for x in (fl.get("topic"), fl.get("detail")) if x))
    concern_block = (
        "\nConcerns a reviewer will likely have (address the most important "
        "one per the acceptance rules):\n"
        + "\n".join(f"- {c}" for c in concerns[:3]) + "\n"
    ) if concerns else ""
    return f"""CANDIDATE WRITING SAMPLES (match this voice):
{samples}

CANDIDATE FACTS (the only facts you may use — career stage: {profile.career_stage}):
{_facts_block(profile)}

Skills: {", ".join(profile.skills)}

JOB: {posting.get("title")} at {posting.get("company")}
{(posting.get("descriptionText") or "")[:5000]}

Why this is a fit (from matching): {reasons}
{concern_block}
Write the cover letter body only — no address block, no date, no
"Dear Hiring Manager" salutation, no signature."""


def _critique_prompt(profile: UserProfile, posting: dict, letter: str) -> str:
    return f"""CANDIDATE FACTS (ground truth):
{_facts_block(profile)}

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


SALARY_RULES = {
    "exact": "restate the target verbatim",
    "range": "present the target as a range (a single number becomes roughly "
             "5% below to 10% above it; an existing range is restated)",
    "negotiable": "say compensation is negotiable depending on the full "
                  "package — do NOT state a number",
}


def _salary_rule(profile: UserProfile) -> str:
    return SALARY_RULES.get(profile.preferences.salary_strategy, SALARY_RULES["exact"])


def _answers_prompt(profile: UserProfile, posting: dict) -> str:
    tri = (lambda v: "not stated"
           if v is None else ("Yes" if v else "No"))
    return f"""Candidate: {profile.name}, {profile.location or "location unspecified"}.
Work authorization: {profile.work_auth or "not stated — leave work_auth null"}.
Salary target: {profile.salary_target or "not stated — leave salary null"}\
 (when answering salary questions: {_salary_rule(profile)}).
Open to relocation: {tri(profile.screeners.open_to_relocation)}.
Willing to work in-person/onsite/hybrid: {tri(profile.screeners.onsite_ok)}.
Skills: {", ".join(profile.skills)}.
{_facts_block(profile)}

Job: {posting.get("title")} at {posting.get("company")}.
Description excerpt: {(posting.get("descriptionText") or "")[:4000]}

Fill the screening answers: why_company (2-3 sentences, specific to this
company, using the posting's own terminology for skills the candidate
genuinely has), salary (follow the salary rule above; null when no target),
work_auth (restate verbatim if provided).

Then reread the description: if it states questions applicants must answer
in their application ("tell us about...", "describe your experience
with...", "include in your application..."), add each one to extra — key =
the question in short form, value = the candidate's answer built ONLY from
the facts above. Skip questions needing facts not stated here, and skip
consent/legal acknowledgments entirely."""
