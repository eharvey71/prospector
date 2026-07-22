"""Title-synonym expansion: preferences.titles -> broader keyword net.

The matching prefilter is a literal keyword check, and job titles vary
wildly for the same work ("grant writer" == "development director" ==
"advancement officer"). One LLM call whenever the user's target titles
change generates the variants; the prefilter then matches against the
expanded set. title_synonyms_source records which titles the synonyms were
built from — the trigger uses it to avoid regenerating (and to avoid
retriggering itself on its own write-back).
"""
from __future__ import annotations

import logging

from google.cloud import firestore
from pydantic import BaseModel, Field

from llm import generate_structured

log = logging.getLogger("synonyms")

MAX_SYNONYMS = 40

SYSTEM = """You expand a job seeker's target titles into the OTHER titles \
employers use for the same or closely adjacent work — across sectors and \
seniority phrasings (e.g. "grant writer" -> "development director", \
"advancement officer", "philanthropy officer", "grants manager", \
"development coordinator"). Short title phrases only, lowercase, no \
duplicates of the originals, no titles for genuinely different work."""


class TitleSynonyms(BaseModel):
    synonyms: list[str] = Field(default_factory=list)


def maybe_expand_titles(db: firestore.Client, uid: str, user_data: dict) -> None:
    """Regenerate synonyms iff titles changed since the last generation.
    Called from the users/{uid} write trigger; the source guard makes the
    write-back a no-op on retrigger."""
    prefs = user_data.get("preferences") or {}
    titles = [t.strip() for t in prefs.get("titles", []) if t and t.strip()]
    if titles == prefs.get("title_synonyms_source", []):
        return  # up to date (or our own write-back)
    if not titles:
        db.collection("users").document(uid).update({
            "preferences.title_synonyms": [],
            "preferences.title_synonyms_source": [],
        })
        return

    skills = ", ".join(user_data.get("skills", [])[:12])
    result = generate_structured(
        f"Target titles: {', '.join(titles)}\n"
        + (f"Seeker's skills (context): {skills}\n" if skills else "")
        + f"\nList up to {MAX_SYNONYMS} synonym titles.",
        TitleSynonyms,
        system=SYSTEM,
        max_tokens=1200,
    )
    seen = {t.lower() for t in titles}
    synonyms = []
    for s in result.synonyms:
        s = s.strip().lower()
        if s and s not in seen:
            seen.add(s)
            synonyms.append(s)

    db.collection("users").document(uid).update({
        "preferences.title_synonyms": synonyms[:MAX_SYNONYMS],
        "preferences.title_synonyms_source": titles,
    })
    log.info("uid=%s titles %s -> %d synonyms", uid, titles, len(synonyms))
