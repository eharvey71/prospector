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

    def fake(prompt, system, max_tokens, temperature, json_mode, model,
             effort):
        seen.update(model=model, effort=effort)
        return ("ok", 3, 4)

    monkeypatch.setattr(llm, "_anthropic", fake)
    monkeypatch.setenv("LLM_MODEL_DRAFTING", "draft-model")
    monkeypatch.setenv("LLM_EFFORT_DRAFTING", "high")
    assert llm.generate("hi", role="drafting") == "ok"
    assert seen == {"model": "draft-model", "effort": "high"}


# --- the OpenAI argument probe --------------------------------------------

class _FakeCompletions:
    """Rejects whichever token parameter the model in question doesn't
    take, the way the real endpoint does — with a message, not a type."""

    def __init__(self, log, rejects):
        self._log, self._rejects = log, rejects

    def __init__(self, log, rejects, reasoning_cost=0):
        self._log, self._rejects = log, rejects
        self._reasoning_cost = reasoning_cost

    def create(self, **kwargs):
        self._log.append(kwargs)
        for bad in self._rejects.get(kwargs["model"], []):
            if bad in kwargs:
                raise RuntimeError(f"Unsupported parameter: '{bad}' is not "
                                   f"supported with this model.")
        # A reasoning model spends the cap on thinking first. If the cap
        # can't cover that, the content comes back empty, not truncated.
        cap = kwargs.get("max_completion_tokens") or kwargs.get("max_tokens")
        if cap < self._reasoning_cost:
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content=""),
                    finish_reason="length")],
                usage=types.SimpleNamespace(prompt_tokens=11,
                                            completion_tokens=cap))
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content="answer"),
                finish_reason="stop")],
            usage=types.SimpleNamespace(prompt_tokens=11, completion_tokens=7))


@pytest.fixture
def fake_openai(monkeypatch):
    log, rejects, tuning = [], {}, {"reasoning_cost": 0}

    class FakeClient:
        def __init__(self, base_url=None):
            self.chat = types.SimpleNamespace(
                completions=_FakeCompletions(log, rejects,
                                             tuning["reasoning_cost"]))

    monkeypatch.setitem(sys.modules, "openai",
                        types.SimpleNamespace(OpenAI=FakeClient))
    monkeypatch.setattr(llm, "_openai_shapes", {})
    monkeypatch.setattr(llm, "PROVIDER", "openai")
    return types.SimpleNamespace(log=log, rejects=rejects, tuning=tuning)


def test_probe_flips_token_param_and_caches(fake_openai, monkeypatch):
    log = fake_openai.log
    fake_openai.rejects["old-style"] = ["max_completion_tokens"]
    monkeypatch.setattr(llm, "MODEL", "old-style")

    assert llm.generate("hi") == "answer"
    assert [k for c in log for k in ("max_completion_tokens", "max_tokens")
            if k in c] == ["max_completion_tokens", "max_tokens"]

    log.clear()
    llm.generate("again")
    # Learned: no second probe, straight to the shape that works.
    assert len(log) == 1 and "max_tokens" in log[0]


def test_probe_is_per_model_not_global(fake_openai, monkeypatch):
    log = fake_openai.log
    fake_openai.rejects["old-style"] = ["max_completion_tokens"]
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


def test_probe_drops_several_rejected_params(fake_openai, monkeypatch):
    fake_openai.rejects["fussy"] = ["temperature", "reasoning_effort"]
    monkeypatch.setattr(llm, "MODEL", "fussy")
    monkeypatch.setenv("LLM_EFFORT", "high")

    assert llm.generate("hi") == "answer"
    final = fake_openai.log[-1]
    assert "temperature" not in final and "reasoning_effort" not in final


def test_json_mode_reaches_the_request(fake_openai, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "m")
    llm.generate("hi", json_mode=True)
    assert fake_openai.log[0]["response_format"] == {"type": "json_object"}


# --- effort ----------------------------------------------------------------

def test_effort_is_per_role(monkeypatch):
    monkeypatch.setenv("LLM_EFFORT", "low")
    monkeypatch.setenv("LLM_EFFORT_DRAFTING", "high")
    assert llm.effort_for("matching") == "low"
    assert llm.effort_for("drafting") == "high"
    assert llm.effort_for() == "low"


def test_effort_unset_is_not_sent(fake_openai, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "m")
    llm.generate("hi")
    assert "reasoning_effort" not in fake_openai.log[0]


def test_effort_reaches_the_request(fake_openai, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "m")
    monkeypatch.setenv("LLM_EFFORT_MATCHING", "medium")
    llm.generate("hi", role="matching")
    assert fake_openai.log[0]["reasoning_effort"] == "medium"


# --- the reasoning-budget trap --------------------------------------------

def test_tight_cap_eaten_by_reasoning_is_retried(fake_openai, monkeypatch):
    """extract and judge are capped at 300 tokens — fine when the cap only
    covered visible output, fatal when it also covers reasoning."""
    fake_openai.tuning["reasoning_cost"] = 900
    monkeypatch.setattr(llm, "MODEL", "thinker")
    monkeypatch.setattr(llm, "REASONING_FLOOR", 4000)

    assert llm.generate("hi", max_tokens=300) == "answer"
    caps = [c.get("max_completion_tokens") or c.get("max_tokens")
            for c in fake_openai.log]
    assert caps == [300, 4000]


def test_retry_bills_both_calls(fake_openai, monkeypatch):
    recorded = {}
    monkeypatch.setattr(llm, "_record_usage",
                        lambda i, o, error=None, model="", role="":
                        recorded.update(input_tokens=i, output_tokens=o))
    fake_openai.tuning["reasoning_cost"] = 900
    monkeypatch.setattr(llm, "MODEL", "thinker")
    monkeypatch.setattr(llm, "REASONING_FLOOR", 4000)

    llm.generate("hi", max_tokens=300)
    # 11 prompt tokens twice; 300 burned on the dead call plus 7 on the
    # good one. A retry that hides its own cost defeats the accounting.
    assert recorded == {"input_tokens": 22, "output_tokens": 307}


def test_spend_is_attributed_to_model_and_role(fake_openai, monkeypatch):
    """Knowing which call site spends the money has to come before any
    decision about which model to move."""
    seen = {}
    monkeypatch.setattr(llm, "_record_usage",
                        lambda i, o, error=None, model="", role="":
                        seen.update(model=model, role=role, tokens=(i, o)))
    monkeypatch.setattr(llm, "MODEL", "m")
    llm.generate("hi", role="matching")
    assert seen == {"model": "m", "role": "matching", "tokens": (11, 7)}


def test_failures_are_attributed_too(fake_openai, monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "_record_usage",
                        lambda i, o, error=None, model="", role="":
                        seen.update(model=model, role=role, error=error))
    fake_openai.rejects["m"] = ["messages"]  # nothing the probe can fix
    monkeypatch.setattr(llm, "MODEL", "m")
    with pytest.raises(RuntimeError):
        llm.generate("hi", role="judge")
    assert seen["role"] == "judge" and seen["model"] == "m"
    assert "messages" in seen["error"]


def test_generous_cap_is_not_retried(fake_openai, monkeypatch):
    fake_openai.tuning["reasoning_cost"] = 900
    monkeypatch.setattr(llm, "MODEL", "thinker")
    llm.generate("hi", max_tokens=3000)
    assert len(fake_openai.log) == 1
