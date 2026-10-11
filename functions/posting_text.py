"""Is this text a job posting, or something pretending to be one?

Kept apart from urljob.py — and free of Firestore/httpx imports — so the
rules can be exercised directly by tests, the same reason letter_guard.py
lives on its own.

Two jobs: recognise a bot-check page served in place of a posting, and
decide whether text a human pasted is enough to score.
"""
from __future__ import annotations

import re

BOT_WALL_RX = re.compile(
    r"confirm you are (not a robot|a human)|are you a human"
    r"|verify (you are|you're) (human|not a robot)"
    r"|checking your browser|just a moment"
    r"|enable javascript( and cookies)? to continue"
    r"|access denied|request (blocked|unsuccessful)"
    r"|unusual traffic|\bcaptcha\b|cf-ray|cloudflare ray id", re.I)

# A wall LEADS with its message, so the opening of the page is the
# reliable signal — some sites wrap the wall in their full nav and footer,
# which defeats a page-length test. A posting that merely mentions
# "captcha" deep in its text still goes through.
BOT_WALL_HEAD_CHARS = 600
BOT_WALL_MAX_CHARS = 1200

# Shorter than this and there is nothing for the rubric to weigh: the
# score would be noise, and a bad score is worse than no posting.
MIN_PASTED_CHARS = 300


def is_bot_wall(text: str) -> bool:
    if BOT_WALL_RX.search(text[:BOT_WALL_HEAD_CHARS]):
        return True
    return len(text) < BOT_WALL_MAX_CHARS and bool(BOT_WALL_RX.search(text))


def is_wall_title(title: str) -> bool:
    """Last line of defence, applied to the EXTRACTED title. Whatever the
    page heuristics miss shows up here: a posting called "Please click to
    confirm you are not a robot" is never a real job."""
    return bool(BOT_WALL_RX.search(title or ""))


def check_pasted(text: str) -> str:
    """Validate text a human pasted in place of a fetch.

    Returns the cleaned text, or raises ValueError with the message the
    UI shows. Pasting is the escape hatch for sites the engine may not
    read, so it must not also become a way to file an empty posting or
    the bot-check page the person was looking at.
    """
    text = (text or "").strip()
    if len(text) < MIN_PASTED_CHARS:
        raise ValueError(
            f"that's only {len(text)} characters — paste the whole posting "
            f"(title, company and the description) so the engine can score "
            f"it properly")
    if is_bot_wall(text):
        raise ValueError(
            "that looks like the bot-check page rather than the posting — "
            "copy the job description itself")
    return text
