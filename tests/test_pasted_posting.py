"""check_pasted(): the way in for sites the engine isn't allowed to read.

LinkedIn blocks reads from cloud IPs outright and an Easy Apply listing
has no employer URL to fall back to, so no amount of retrying helps.
Pasting the posting is the only route that works — and it must not also
become a way to file an empty posting, or the bot-check page the person
was looking at when the fetch failed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions"))

import pytest  # noqa: E402

from posting_text import check_pasted, is_bot_wall, is_wall_title  # noqa: E402

REAL = ("Editorial Assistant, American Association for Justice. We are "
        "seeking an editorial assistant to support our quarterly journal. "
        "Responsibilities include manuscript tracking, copy editing, and "
        "coordinating peer review correspondence with contributors. ") * 3


def test_accepts_a_real_posting():
    assert check_pasted(REAL) == REAL.strip()


def test_rejects_text_too_short_to_score():
    with pytest.raises(ValueError, match="paste the whole posting"):
        check_pasted("Editorial Assistant")


def test_the_message_says_how_short_it_was():
    with pytest.raises(ValueError, match=r"only 19 characters"):
        check_pasted("Editorial Assistant")


def test_rejects_a_pasted_bot_check_page():
    wall = "Please click to confirm you are not a robot. " * 12
    with pytest.raises(ValueError, match="bot-check"):
        check_pasted(wall)


def test_rejects_empty_and_none():
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            check_pasted(bad)


def test_a_posting_that_merely_mentions_captcha_is_fine():
    # The word appears deep in a full-length posting, not at the top of a
    # wall. Note the floor: the length rule only spares text past
    # BOT_WALL_MAX_CHARS, so a SHORT paste mentioning captcha is still
    # refused — acceptable, since a real pasted description is long.
    text = (REAL * 2) + " Experience with CAPTCHA tooling is a plus."
    assert len(text) > 1200
    assert not is_bot_wall(text)
    assert check_pasted(text)


def test_wall_title_still_catches_an_extracted_title():
    assert is_wall_title("Please click to confirm you are not a robot")
    assert not is_wall_title("Editorial Assistant")


# --- does the posting ask for a cover letter? -----------------------------
# Advisory only: it says what the posting SAYS, so the person can decide
# whether a draft is worth paying for. A regex, not an LLM field, because
# crawled postings never reach the extraction step and that is where
# nearly all the volume is.

from posting_text import cover_letter_signal  # noqa: E402


@pytest.mark.parametrize("text", [
    "Please submit a resume and cover letter through our portal.",
    "A cover letter is required for consideration.",
    "Applicants must include a cover letter describing their interest.",
    "Cover letter and resume required.",
])
def test_required_phrasings(text):
    assert cover_letter_signal(text) == "required"


@pytest.mark.parametrize("text", [
    "A cover letter is optional but encouraged.",
    "You may also attach a cover letter.",
    "Cover letters are welcome.",
])
def test_optional_phrasings(text):
    assert cover_letter_signal(text) == "optional"


@pytest.mark.parametrize("text", [
    "A cover letter is not required for this role.",
    "Please do not submit a cover letter.",
    "No cover letter necessary.",
    "Resumes only, please.",
])
def test_not_wanted_phrasings(text):
    assert cover_letter_signal(text) == "not_wanted"


def test_negation_beats_the_requirement_phrasing():
    # "is not required" contains "required" — order of the checks matters.
    assert cover_letter_signal("A cover letter is not required.") == "not_wanted"


def test_silence_is_reported_as_silence():
    assert cover_letter_signal(REAL) == "unstated"
    assert cover_letter_signal("") == "unstated"
