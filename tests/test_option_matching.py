"""option_matches() guards against the v2.2 sponsorship incident: a 'No'
answer selecting 'Yes, but not one of the visas listed here'."""
from common import option_matches


def test_exact_match():
    assert option_matches("No", "No")
    assert option_matches("Yes", "Yes")
    assert option_matches("United States", "United States")


def test_trailing_punctuation():
    assert option_matches("No", "No.")
    assert option_matches("Yes", "Yes!")


def test_polarity_never_crosses():
    # The incident case, verbatim shape:
    assert not option_matches("No", "Yes, but not one of the visas listed here")
    assert not option_matches("Yes", "No, I do not require sponsorship")
    assert not option_matches("No", "Yes")
    assert not option_matches("Yes", "No.")


def test_prefix_requires_whole_word():
    # "No" must not match "Not at this time" / "None of the above"
    assert not option_matches("No", "Not at this time")
    assert not option_matches("No", "None of the above")
    # But a genuine elaboration keeps polarity and starts with the word:
    assert option_matches("No", "No, I do not")
    assert option_matches("Yes", "Yes, I am authorized")


def test_case_insensitive():
    assert option_matches("no", "No")
    assert option_matches("YES", "yes")
