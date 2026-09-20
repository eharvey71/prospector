"""Provider-agnostic LLM access.

One function: generate(). Provider and model come from env vars, so
switching between Anthropic, Vertex and anything OpenAI-compatible is a
config change — no caller knows which model is answering.

Env:
    LLM_PROVIDER   anthropic | vertex | openai        (default: anthropic)
    LLM_MODEL      the provider's model id, verbatim
    ANTHROPIC_API_KEY                                 (anthropic)
    GOOGLE_CLOUD_PROJECT [, VERTEX_LOCATION]          (vertex)
    OPENAI_API_KEY [, OPENAI_BASE_URL]                (openai)
    LLM_DAILY_TOKEN_BUDGET   spend tripwire; 0 disables
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic")
MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")

# Hard daily ceiling on total tokens (input + output), enforced by
# generate() against the health/llm_YYYYMMDD accounting doc. 0 disables.
DAILY_TOKEN_BUDGET = int(os.environ.get("LLM_DAILY_TOKEN_BUDGET", "2000000"))

log = logging.getLogger("llm")


class BudgetExceeded(RuntimeError):
    """Raised instead of calling the provider once today's spend passes
    DAILY_TOKEN_BUDGET. Every caller already survives a failed generate():
    matching skips the posting, the crawl isolates the source, the judge
    returns 'unclear'. Resets at UTC midnight (new accounting doc)."""

_health_db = None  # lazy firestore client, shared across calls


def _record_usage(input_tokens: int, output_tokens: int,
                  error: str | None = None) -> None:
    """Best-effort spend/error accounting into the global `health`
    collection (cumulative doc + per-day doc). The engine hit a provider
    spend cap once with zero visibility — never again. Must never break a
    real call."""
    global _health_db
    try:
        from google.cloud import firestore
        if _health_db is None:
            _health_db = firestore.Client()
        now = datetime.now(timezone.utc)
        counters: dict[str, Any] = {
            "calls": firestore.Increment(1),
            "input_tokens": firestore.Increment(input_tokens),
            "output_tokens": firestore.Increment(output_tokens),
            "lastCallAt": now,
        }
        if error:
            counters["errors"] = firestore.Increment(1)
            counters["lastErrorAt"] = now
            counters["lastError"] = error[:300]
        _health_db.collection("health").document("llm").set(counters, merge=True)
        _health_db.collection("health").document(
            f"llm_{now.strftime('%Y%m%d')}").set(counters, merge=True)
    except Exception:
        log.debug("llm usage recording failed", exc_info=True)


_budget_cache = {"stamp": 0.0, "over": False}


def _over_budget() -> bool:
    """True once today's recorded spend passes DAILY_TOKEN_BUDGET.

    Firestore read cached for 60s so the check adds one read per minute,
    not per call. Fails OPEN: if the accounting doc can't be read, the
    call proceeds — the budget is a tripwire against runaway spend, and
    must never be the thing that breaks a healthy pipeline."""
    if DAILY_TOKEN_BUDGET <= 0:
        return False
    import time
    now = time.monotonic()
    if now - _budget_cache["stamp"] < 60:
        return _budget_cache["over"]
    over = False
    try:
        from google.cloud import firestore  # noqa: F401
        global _health_db
        if _health_db is None:
            _health_db = firestore.Client()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        d = _health_db.collection("health").document(f"llm_{day}").get().to_dict() or {}
        spent = int(d.get("input_tokens", 0)) + int(d.get("output_tokens", 0))
        over = spent >= DAILY_TOKEN_BUDGET
        if over:
            log.warning("LLM daily budget exhausted: %d/%d tokens",
                        spent, DAILY_TOKEN_BUDGET)
    except Exception:
        log.debug("budget check failed; allowing call", exc_info=True)
    _budget_cache.update(stamp=now, over=over)
    return over


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    json_mode: bool = False,
) -> str:
    """Single-turn text generation. json_mode asks providers that support
    it to guarantee syntactically valid JSON (others ignore it and lean on
    the prompt plus the repair pass in generate_structured)."""
    if _over_budget():
        raise BudgetExceeded(
            f"daily LLM token budget ({DAILY_TOKEN_BUDGET}) exhausted; "
            f"resets at UTC midnight, or raise LLM_DAILY_TOKEN_BUDGET")
    if PROVIDER == "anthropic":
        fn = _anthropic
    elif PROVIDER == "vertex":
        fn = _vertex
    elif PROVIDER in ("openai", "openai_compatible"):
        fn = _openai
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {PROVIDER}")
    try:
        text, tokens_in, tokens_out = fn(prompt, system, max_tokens,
                                         temperature, json_mode)
    except Exception as exc:
        _record_usage(0, 0, error=f"{type(exc).__name__}: {exc}")
        raise
    _record_usage(tokens_in, tokens_out)
    return text


def generate_structured(
    prompt: str,
    schema: Type[T],
    *,
    system: Optional[str] = None,
    max_tokens: int = 2000,
) -> T:
    """Generation constrained to a Pydantic schema, with fence-stripping and
    one automatic repair attempt on parse failure."""
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    full_system = (
        (system + "\n\n" if system else "")
        + "Respond with ONLY a JSON object matching this schema. "
        + "No prose, no markdown fences.\n" + schema_json
    )
    raw = generate(prompt, system=full_system, max_tokens=max_tokens,
                   temperature=0.2, json_mode=True)
    try:
        return schema.model_validate_json(_strip_fences(raw))
    except Exception:
        repair = generate(
            f"The following was supposed to be valid JSON for the schema but "
            f"failed to parse. Output ONLY the corrected JSON.\n\n{raw}",
            system=full_system,
            max_tokens=max_tokens,
            temperature=0.0,
            json_mode=True,
        )
        return schema.model_validate_json(_strip_fences(repair))


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

_sdk_takes_temperature: Optional[bool] = None


def _anthropic(prompt: str, system: Optional[str], max_tokens: int,
               temperature: float, json_mode: bool = False) -> tuple[str, int, int]:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    # anthropic 1.x REMOVED the temperature parameter from
    # Messages.create (an unbounded >=0.40 pin pulled the new major in,
    # and every call started failing client-side with a TypeError).
    # Feature-detect once so both SDK generations work.
    global _sdk_takes_temperature
    if _sdk_takes_temperature is None:
        import inspect
        _sdk_takes_temperature = "temperature" in inspect.signature(
            type(client.messages).create).parameters
    kwargs: dict[str, Any] = dict(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if _sdk_takes_temperature:
        kwargs["temperature"] = temperature
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    text = "".join(block.text for block in resp.content if block.type == "text")
    usage = getattr(resp, "usage", None)
    return (text,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0)


def _vertex(prompt: str, system: Optional[str], max_tokens: int,
            temperature: float, json_mode: bool = False) -> tuple[str, int, int]:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("VERTEX_LOCATION", "us-central1"),
    )
    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    meta = getattr(resp, "usage_metadata", None)
    return (resp.text or "",
            getattr(meta, "prompt_token_count", 0) or 0,
            getattr(meta, "candidates_token_count", 0) or 0)


# Which argument shape this endpoint accepts, learned from its own error
# messages on the first call. The GPT-5 generation renamed max_tokens to
# max_completion_tokens and rejects a non-default temperature; older
# models and most compatible endpoints do the opposite. Probing beats
# hard-coding a table of model names that goes stale.
_openai_shape = {"token_param": None, "temperature": None}


def _openai(prompt: str, system: Optional[str], max_tokens: int,
            temperature: float, json_mode: bool = False) -> tuple[str, int, int]:
    """OpenAI and any OpenAI-compatible endpoint.

    Env:
        OPENAI_API_KEY    the key
        OPENAI_BASE_URL   optional; point at a compatible gateway
        LLM_MODEL         the model id, verbatim from the provider's docs
    """
    from openai import OpenAI

    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None)
    messages = ([{"role": "system", "content": system}] if system else []) \
        + [{"role": "user", "content": prompt}]

    def call(token_param: str, send_temperature: bool):
        kwargs: dict[str, Any] = {
            "model": MODEL,
            "messages": messages,
            token_param: max_tokens,
        }
        if send_temperature:
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    token_param = _openai_shape["token_param"] or "max_completion_tokens"
    send_temp = _openai_shape["temperature"]
    send_temp = True if send_temp is None else send_temp
    try:
        resp = call(token_param, send_temp)
    except Exception as exc:
        msg = str(exc)
        retried = False
        # "Unsupported parameter: 'max_completion_tokens'" (or the reverse)
        if "max_completion_tokens" in msg or "max_tokens" in msg:
            token_param = ("max_tokens" if token_param == "max_completion_tokens"
                           else "max_completion_tokens")
            retried = True
        # "Unsupported value: 'temperature' does not support 0.7"
        if "temperature" in msg:
            send_temp = False
            retried = True
        if not retried:
            raise
        resp = call(token_param, send_temp)
    _openai_shape["token_param"] = token_param
    _openai_shape["temperature"] = send_temp

    text = (resp.choices[0].message.content or "") if resp.choices else ""
    usage = getattr(resp, "usage", None)
    return (text,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0)
