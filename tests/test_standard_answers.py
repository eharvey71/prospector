"""decide_standard_answer(): profile facts in, deterministic answers out —
and None (escalate) for anything requiring judgment or missing facts."""
from common import decide_standard_answer

US = dict(location="Arlington, VA", work_auth="US Citizen")


def ans(label, *, location=US["location"], work_auth=US["work_auth"],
        screeners=None):
    return decide_standard_answer(label, location, work_auth, screeners)


def test_sponsorship():
    assert ans("Will you require visa sponsorship?") == "No"
    # Unknown authorization -> escalate, never guess:
    assert ans("Will you require visa sponsorship?", work_auth="") is None


def test_work_authorization():
    assert ans("Are you legally authorized to work in the United States?") == "Yes"
    assert ans("Are you authorized to work in the US?", work_auth="") is None


def test_citizenship():
    assert ans("Are you a U.S. citizen?") == "Yes"
    assert ans("Are you a citizen of the United States?",
               work_auth="green card holder") == "No"
    assert ans("Are you a citizen?", work_auth="") is None


def test_country_of_residence():
    assert ans("What is your country of residence?") == "United States"
    assert ans("Country of residence?", location="Toronto, Canada") is None


def test_state_hints_are_word_bounded():
    """Regression: ', ca' must mean California, not Canada; ', co' must
    mean Colorado, not Colombia. Substring matching got this wrong."""
    q = "What is your country of residence?"
    assert ans(q, location="Toronto, Canada") is None
    assert ans(q, location="Bogotá, Colombia") is None
    assert ans(q, location="Busan, South Korea") is None  # 'usa' inside 'Busan'
    assert ans(q, location="San Francisco, CA") == "United States"
    assert ans(q, location="Denver, CO") == "United States"
    assert ans(q, location="Arlington, VA 22201") == "United States"
    assert ans(q, location="Remote, USA") == "United States"


def test_screener_facts_three_valued():
    # Stated facts answer; missing facts escalate.
    assert ans("Are you open to relocation?",
               screeners={"open_to_relocation": True}) == "Yes"
    assert ans("Are you willing to relocate?",
               screeners={"open_to_relocation": False}) == "No"
    assert ans("Are you open to relocation?", screeners={}) is None
    assert ans("Can you work on-site in our office?",
               screeners={"onsite_ok": True}) == "Yes"
    assert ans("Is a hybrid schedule acceptable?", screeners={}) is None


def test_judgment_questions_escalate():
    assert ans("Why do you want to work here?") is None
    assert ans("Rate your Python skills from 1-10") is None
    assert ans("Have you previously been employed by Acme?") is None
