"""wrong_employer(): the backstop for a letter written about another job.

The failure it exists for: a past cover letter saved as a "writing
sample" leaks its employer into a new letter. It must catch that without
firing on letters that legitimately mention past employers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions"))

from letter_guard import wrong_employer  # noqa: E402
from schemas import UserProfile, WorkHistoryItem, WritingSample  # noqa: E402

SAMPLE_LETTER = WritingSample(
    title="Cover letter - Elastic",
    text="I am writing to apply for the Solutions Engineer role at Elastic. "
         "My work with search infrastructure at Unicon shaped how I think "
         "about developer experience.",
)


def profile(**kw) -> UserProfile:
    base = dict(
        name="Eric Harvey", email="e@example.com",
        work_history=[WorkHistoryItem(company="Unicon", title="Engineer",
                                      start="2019-01")],
        writing_samples=[SAMPLE_LETTER],
    )
    base.update(kw)
    return UserProfile(**base)


POSTING = {"company": "anthropic", "title": "Applied AI Architect"}


def test_catches_employer_borrowed_from_a_sample():
    letter = ("Elastic's focus on search-powered solutions aligns with what "
              "I have been building toward for years.")
    assert wrong_employer(letter, POSTING, profile()) == "Elastic"


def test_catches_a_past_employer_standing_in_for_the_target():
    letter = "I would be glad to bring that experience to Unicon."
    assert wrong_employer(letter, POSTING, profile()) == "Unicon"


def test_silent_when_the_right_company_is_named():
    # Past employers may be discussed freely once the target is named —
    # that's ordinary cover-letter content, not a wrong-job letter.
    letter = ("At Unicon I ran the platform team, and Anthropic's work on "
              "interpretability is why I'm writing.")
    assert wrong_employer(letter, POSTING, profile()) is None


def test_slug_companies_match_their_spaced_form():
    # Crawled postings store the board slug: "capitalone" vs "Capital One".
    posting = {"company": "capitalone", "title": "Manager, Project Management"}
    letter = "Capital One's card organization is where I want to do this work."
    assert wrong_employer(letter, posting, profile()) is None


def test_silent_when_no_employer_is_named_at_all():
    letter = "I have spent six years building developer platforms."
    assert wrong_employer(letter, POSTING, profile()) is None


def test_silent_when_the_posting_company_is_unusable():
    for company in ("unknown", "", "careerhq.asaecenter.org"):
        letter = "Elastic is a wonderful company."
        assert wrong_employer(letter, {"company": company}, profile()) is None
