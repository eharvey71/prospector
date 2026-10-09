"""Deterministic guard: is this letter about the job it's for?

Kept apart from drafting.py — and free of Firestore/LLM imports — so the
rule can be exercised directly by tests. The failure it exists for: a
past cover letter saved as a "writing sample" leaks its employer into a
new letter, and the letter goes out addressed to the wrong company.
"""
from __future__ import annotations

import re

from schemas import UserProfile


def _squash(s: str) -> str:
    """Letters-and-digits only, lowercased. Crawled postings store the board
    SLUG as the company ("capitalone", "northeastern"), so a letter saying
    "Capital One" only matches once the spaces are gone."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# Company values that name no employer at all: unknown, or the host we fell
# back to when extraction failed. Nothing to check against.
_VAGUE_COMPANY = re.compile(
    r"^\s*(unknown|n/?a)?\s*$|\.(com|org|net|edu|gov)\b", re.I)

# "at Acme", "with Acme Corp", "join Acme" — how a cover letter names the
# company it was written for. The capture runs to three words because
# company names do ("Capital One", "Virginia Commonwealth University"),
# which means it also runs past a sentence end ("at Elastic. My work…").
# _candidates() below trims that back down.
_NAMED_EMPLOYER = re.compile(
    r"\b(?:at|with|join|joining|for)\s+"
    r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})")

# Capitalized words that follow a sentence end and are never a company.
_NOT_A_NAME = {"i", "my", "the", "a", "an", "this", "it", "we", "they",
               "his", "her", "their", "that", "these", "those"}

# Joining words that carry no identity inside a company name.
_FILLER = {"of", "the", "and", "for", "at", "a", "an"}


def company_aliases(company: str) -> tuple[set[str], set[str]]:
    """How a letter might actually write this employer's name.

    Returns (squashed_forms, acronyms). Long institutional names are the
    reason this exists: nobody writes "Virginia Commonwealth University
    School of Medicine" in a sentence — they write "Virginia Commonwealth
    University" or "VCU". Demanding the full string meant the "right
    employer is named" escape hatch below never opened for those, so any
    past employer the letter legitimately mentioned tripped the guard.

    Only LEADING runs count. The distinctive part of a company name comes
    first, and trailing runs are generic enough to open the hatch wrongly
    ("School of Medicine" would match half the postings in healthcare).
    """
    words = [w for w in re.findall(r"[A-Za-z0-9&]+", company or "")
             if w.lower() not in _FILLER]
    forms = {_squash("".join(words[:n])) for n in range(2, len(words) + 1)}
    # "VCU" for Virginia Commonwealth University. Matched with word
    # boundaries against the raw letter, never against the squashed body,
    # where three letters would collide with the inside of other words.
    acronyms = {"".join(w[0] for w in words[:n]).upper()
                for n in range(3, len(words) + 1)}
    return ({f for f in forms if len(f) >= 6},
            {a for a in acronyms if len(a) >= 3})


def _candidates(phrase: str) -> list[str]:
    """Every sensible company name inside a captured phrase.

    "Elastic. My work" yields "Elastic" — a name is cut short by the
    period that ended its sentence, so each prefix is offered and the
    caller matches whichever actually appears in the letter."""
    out: list[str] = []
    words: list[str] = []
    for raw in phrase.split():
        word = raw.strip(".,;:!?()[]\"'")
        if not word or word.lower() in _NOT_A_NAME:
            break
        words.append(word)
        out.append(" ".join(words))
        if raw.rstrip('"\')]').endswith((".", ",", ";", ":", "!", "?")):
            break          # the name ended here; anything after is prose
    return out


def wrong_employer(letter: str, posting: dict, profile: UserProfile) -> str | None:
    """The employer named in the letter isn't the one being applied to.

    Returns the offending name, or None when the letter looks right. The
    check is deliberately narrow: it only fires when the posting's company
    is ABSENT and some OTHER employer — one of the candidate's past
    employers, or a company named in their writing samples — is present.
    That is exactly the shape of a letter that borrowed an old cover
    letter's subject, and it cannot fire on a letter that simply never
    names anyone.
    """
    company = (posting.get("company") or "").strip()
    if _VAGUE_COMPANY.search(company):
        return None
    body = _squash(letter)
    if not body or _squash(company) in body:
        return None            # the right employer is named: fine

    # ...or named the way a person would write it. Without this, a letter
    # correctly addressed to "VCU School of Medicine" counted as naming
    # nobody, and the writer's own job history became the stray.
    forms, acronyms = company_aliases(company)
    if any(f in body for f in forms):
        return None
    if any(re.search(rf"\b{re.escape(a)}\b", letter) for a in acronyms):
        return None

    others = [w.company for w in profile.work_history if w.company]
    for sample in profile.writing_samples[:3]:
        for phrase in _NAMED_EMPLOYER.findall(sample.text[:2000]):
            others += _candidates(phrase)
    for name in others:
        squashed = _squash(name)
        if len(squashed) >= 4 and squashed in body:
            return name.strip()
    return None
