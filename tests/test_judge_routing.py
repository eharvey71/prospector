"""confirm_submission() routing: deterministic tiers, judge verdicts, and
the invariant that failure always degrades to 'unclear' — never to success,
never to anything retryable."""
import asyncio
import sys
import types

import common
from common import SubmitVerdict, confirm_submission


class FakePage:
    def __init__(self, url="https://boards.example.com/apply", body=""):
        self.url = url
        self._body = body

    async def evaluate(self, _script):
        return self._body


class BrokenPage(FakePage):
    async def evaluate(self, _script):
        raise RuntimeError("Execution context was destroyed")


def stub_llm(result=None, exc=None, calls=None):
    """Install a fake `llm` module for confirm_submission's lazy import."""
    mod = types.ModuleType("llm")

    def generate_structured(prompt, schema, *, system=None, max_tokens=2000):
        if calls is not None:
            calls.append(prompt)
        if exc:
            raise exc
        return result

    mod.generate_structured = generate_structured
    sys.modules["llm"] = mod


def run(page):
    return asyncio.run(confirm_submission(page, title="SWE", company="Acme"))


def test_confirmation_url_skips_judge():
    calls = []
    stub_llm(calls=calls, exc=AssertionError("judge must not be called"))
    verdict, _ = run(FakePage(url="https://jobs.lever.co/acme/x/thanks"))
    assert verdict == "submitted" and not calls


def test_confirmation_phrase_skips_judge():
    calls = []
    stub_llm(calls=calls, exc=AssertionError("judge must not be called"))
    verdict, _ = run(FakePage(body="Thank you for applying! We'll be in touch."))
    assert verdict == "submitted" and not calls


def test_bare_thank_you_goes_to_judge():
    """Job descriptions say 'thank you for your interest' — that phrase on a
    still-visible form must NOT deterministically count as submitted."""
    calls = []
    stub_llm(SubmitVerdict(verdict="not_submitted", confidence=0.95,
                           reason="required-field errors visible"), calls=calls)
    verdict, _ = run(FakePage(
        body="Thank you for your interest in Acme.\n"
             "First name is required.\nSubmit application"))
    assert verdict == "not_submitted" and calls


def test_empty_body_is_unclear_without_judge():
    calls = []
    stub_llm(calls=calls, exc=AssertionError("judge must not be called"))
    verdict, _ = run(FakePage(body="  \n "))
    assert verdict == "unclear" and not calls


def test_confident_judge_submitted():
    stub_llm(SubmitVerdict(verdict="submitted", confidence=0.92, reason="receipt"))
    assert run(FakePage(body="Submission complete. Next steps..."))[0] == "submitted"


def test_unsure_judge_downgrades_to_unclear():
    stub_llm(SubmitVerdict(verdict="submitted", confidence=0.4, reason="maybe"))
    assert run(FakePage(body="Loading..."))[0] == "unclear"


def test_judge_crash_degrades_to_unclear():
    stub_llm(exc=RuntimeError("API down"))
    assert run(FakePage(body="Some page text"))[0] == "unclear"


def test_broken_page_degrades_to_unclear():
    stub_llm(exc=AssertionError("judge must not be called"))
    assert run(BrokenPage())[0] == "unclear"


def test_confirm_rx_precision():
    should_match = [
        "Thank you for applying to Acme",
        "Thank you for your application!",
        "Your application has been submitted.",
        "application was received",
        "We've received your application",
        "We have received your application",
    ]
    should_not = [
        "Thank you for your interest in Acme",
        "Submit application",
        "Applications received by Friday get priority",
        "thank you",
    ]
    for s in should_match:
        assert common.CONFIRM_RX.search(s), f"should match: {s}"
    for s in should_not:
        assert not common.CONFIRM_RX.search(s), f"must not match: {s}"
