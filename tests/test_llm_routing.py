"""Per-call-site provider/model routing, and the OpenAI argument probe.

The point of the role override is running two models side by side, so the
things worth testing are: a role redirects only itself, and one model's
probe result never leaks onto another's.
"""
import os
import sys
import types

import pytest

import llm


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Roles read the environment at call time. Clear anything the host
    already exported so the suite doesn't depend on the dev's shell."""
    for key in list(os.environ):
        if key.startswith(("LLM_MODEL_", "LLM_PROVIDER_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(llm, "PROVIDER", "anthropic")
    monkeypatch.setattr(llm, "MODEL", "claude-sonnet-4-6")


# --- resolve() -------------------------------------------------------------

def test_no_role_uses_globals():
    assert llm.resolve() == ("anthropic", "claude-sonnet-4-6")
    assert llm.resolve("matching") == ("anthropic", "claude-sonnet-4-6")


def test_role_model_override_is_scoped(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_MATCHING", "some-other-model")
    assert llm.resolve("matching") == ("anthropic", "some-other-model")
    # Every other call site is untouched — that is the whole feature.
    assert llm.resolve("drafting") == ("anthropic", "claude-sonnet-4-6")
    assert llm.resolve() == ("anthropic", "claude-sonnet-4-6")


def test_role_can_switch_vendor(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_JUDGE", "openai")
    monkeypatch.setenv("LLM_MODEL_JUDGE", "some-openai-model")
    assert llm.resolve("judge") == ("openai", "some-openai-model")
    assert llm.resolve("drafting") == ("anthropic", "claude-sonnet-4-6")


def test_role_name_is_normalised(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_RESUME_TAILOR", "m")
    assert llm.resolve("resume_tailor")[1] == "m"
    assert llm.resolve("resume-tailor")[1] == "m"


def test_empty_env_value_falls_back(monkeypatch):
    # An exported-but-blank var is a deploy accident, not an instruction to
    # call the empty-string model.
    monkeypatch.setenv("LLM_MODEL_MATCHING", "")
    assert llm.resolve("matching")[1] == "claude-sonnet-4-6"


def test_unknown_provider_names_the_role(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_JUDGE", "typo")
    with pytest.raises(ValueError, match="judge"):
        llm.generate("hi", role="judge")


# --- dispatch --------------------------------------------------------------

def test_generate_passes_resolved_model_to_provider(monkeypatch):
    seen = {}

    def fake(prompt, system, max_tokens, temperature, json_mode, model):
        seen["model"] = model
        return ("ok", 3, 4)

    monkeypatch.setattr(llm, "_anthropic", fake)
    monkeypatch.setenv("LLM_MODEL_DRAFTING", "draft-model")
    assert llm.generate("hi", role="drafting") == "ok"
    assert seen["model"] == "draft-model"


# --- the OpenAI argument probe --------------------------------------------

class _FakeCompletions:
    """Rejects whichever token parameter the model in question doesn't
    take, the way the real endpoint does — with a message, not a type."""

    def __init__(self, log, rejects):
        self._log, self._rejects = log, rejects

    def create(self, **kwargs):
        self._log.append(kwargs)
        bad = self._rejects.get(kwargs["model"])
        if bad and bad in kwargs:
            raise RuntimeError(f"Unsupported parameter: '{bad}' is not "
                               f"supported with this model.")
        msg = types.SimpleNamespace(content="answer")
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg)],
            usage=types.SimpleNamespace(prompt_tokens=11, completion_tokens=7))


@pytest.fixture
def fake_openai(monkeypatch):
    log, rejects = [], {}

    class FakeClient:
        def __init__(self, base_url=None):
            self.chat = types.SimpleNamespace(
                completions=_FakeCompletions(log, rejects))

    monkeypatch.setitem(sys.modules, "openai",
                        types.SimpleNamespace(OpenAI=FakeClient))
    monkeypatch.setattr(llm, "_openai_shapes", {})
    monkeypatch.setattr(llm, "PROVIDER", "openai")
    return log, rejects


def test_probe_flips_token_param_and_caches(fake_openai, monkeypatch):
    log, rejects = fake_openai
    rejects["old-style"] = "max_completion_tokens"
    monkeypatch.setattr(llm, "MODEL", "old-style")

    assert llm.generate("hi") == "answer"
    assert [k for c in log for k in ("max_completion_tokens", "max_tokens")
            if k in c] == ["max_completion_tokens", "max_tokens"]

    log.clear()
    llm.generate("again")
    # Learned: no second probe, straight to the shape that works.
    assert len(log) == 1 and "max_tokens" in log[0]


def test_probe_is_per_model_not_global(fake_openai, monkeypatch):
    log, rejects = fake_openai
    rejects["old-style"] = "max_completion_tokens"
    monkeypatch.setattr(llm, "MODEL", "old-style")
    monkeypatch.setenv("LLM_MODEL_JUDGE", "new-style")  # takes the new param

    llm.generate("hi")                    # teaches the cache about old-style
    log.clear()
    assert llm.generate("hi", role="judge") == "answer"
    # new-style must NOT inherit old-style's answer; it takes the new
    # parameter and should succeed on the first call.
    assert len(log) == 1
    assert "max_completion_tokens" in log[0]
    assert log[0]["model"] == "new-style"


def test_json_mode_reaches_the_request(fake_openai, monkeypatch):
    log, _ = fake_openai
    monkeypatch.setattr(llm, "MODEL", "m")
    llm.generate("hi", json_mode=True)
    assert log[0]["response_format"] == {"type": "json_object"}
