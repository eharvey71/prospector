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

Per-call-site override: every call passes a role (matching, drafting,
judge, agent, ...). LLM_PROVIDER_<ROLE> and LLM_MODEL_<ROLE> override the
globals for that role alone, so one part of the pipeline can be moved to
a new model while the rest stays put. Spend is recorded per model per
day, which is what makes the comparison worth anything.
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

# Retry ceiling for the openai provider when a reasoning model spends its
# whole token cap on reasoning and returns nothing. See _openai().
REASONING_FLOOR = int(os.environ.get("LLM_REASONING_FLOOR", "4000"))

log = logging.getLogger("llm")


def effort_for(role: Optional[str] = None) -> Optional[str]:
    """Reasoning effort for a call site, or None to let the provider decide.

    Only the openai provider uses it. Benchmarks for these models are
    quoted at a specific effort, and the gap between settings is large in
    both quality and latency — so it has to be a knob, not a default we
    inherit silently. LLM_EFFORT_<ROLE> beats LLM_EFFORT."""
    if role:
        key = re.sub(r"[^A-Z0-9]", "_", role.upper())
        per_role = os.environ.get(f"LLM_EFFORT_{key}")
        if per_role:
            return per_role
    return os.environ.get("LLM_EFFORT") or None


def resolve(role: Optional[str] = None) -> tuple[str, str]:
    """(provider, model) for one call site.

    A role is just a name — "matching", "drafting", "judge". Setting
    LLM_MODEL_JUDGE moves the judge to another model and leaves everything
    else alone; setting LLM_PROVIDER_JUDGE moves it to another vendor
    entirely. Nothing set means the global LLM_PROVIDER/LLM_MODEL, which
    is the normal case."""
    if not role:
        return PROVIDER, MODEL
    key = re.sub(r"[^A-Z0-9]", "_", role.upper())
    return (os.environ.get(f"LLM_PROVIDER_{key}") or PROVIDER,
            os.environ.get(f"LLM_MODEL_{key}") or MODEL)


class BudgetExceeded(RuntimeError):
    """Raised instead of calling the provider once today's spend passes
    DAILY_TOKEN_BUDGET. Every caller already survives a failed generate():
    matching skips the posting, the crawl isolates the source, the judge
    returns 'unclear'. Resets at UTC midnight (new accounting doc)."""

_health_db = None  # lazy firestore client, shared across calls


def _safe_doc_id(text: str) -> str:
    """Firestore document ids can't contain '/'. Model ids increasingly
    carry dots and slashes (vendor/model-1.2), so flatten them."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)[:200]


def _record_usage(input_tokens: int, output_tokens: int,
                  error: str | None = None, model: str = "",
                  role: str = "") -> None:
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
        if model:
            counters["lastModel"] = model
        day = now.strftime("%Y%m%d")
        _health_db.collection("health").document("llm").set(counters, merge=True)
        _health_db.collection("health").document(f"llm_{day}").set(
            counters, merge=True)
        # Per model and per call site, per day. The budget still counts the
        # whole pipeline together (above); these two break it down.
        #
        # Per model: makes two models running side by side comparable.
        # Per role: answers the question that has to come FIRST — which
        # call site is actually spending the money. Swapping a model to
        # save money without this is guesswork.
        for prefix, value in (("model", model), ("role", role)):
            if value:
                _health_db.collection("health").document(
                    f"llm_{prefix}_{_safe_doc_id(value)}_{day}").set(
                        {**counters, prefix: value}, merge=True)
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
    role: Optional[str] = None,
) -> str:
    """Single-turn text generation. json_mode asks providers that support
    it to guarantee syntactically valid JSON (others ignore it and lean on
    the prompt plus the repair pass in generate_structured). role names the
    call site so LLM_PROVIDER_<ROLE>/LLM_MODEL_<ROLE> can redirect it."""
    if _over_budget():
        raise BudgetExceeded(
            f"daily LLM token budget ({DAILY_TOKEN_BUDGET}) exhausted; "
            f"resets at UTC midnight, or raise LLM_DAILY_TOKEN_BUDGET")
    provider, model = resolve(role)
    if provider == "anthropic":
        fn = _anthropic
    elif provider == "vertex":
        fn = _vertex
    elif provider in ("openai", "openai_compatible"):
        fn = _openai
    else:
        raise ValueError(
            f"Unknown LLM provider {provider!r}"
            + (f" for role {role!r}" if role else ""))
    try:
        text, tokens_in, tokens_out = fn(prompt, system, max_tokens,
                                         temperature, json_mode, model,
                                         effort_for(role))
    except Exception as exc:
        _record_usage(0, 0, error=f"{type(exc).__name__}: {exc}",
                      model=model, role=role or "")
        raise
    _record_usage(tokens_in, tokens_out, model=model, role=role or "")
    return text


def generate_structured(
    prompt: str,
    schema: Type[T],
    *,
    system: Optional[str] = None,
    max_tokens: int = 2000,
    role: Optional[str] = None,
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
                   temperature=0.2, json_mode=True, role=role)
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
            role=role,
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
               temperature: float, json_mode: bool = False,
               model: str = "",
               effort: Optional[str] = None) -> tuple[str, int, int]:
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
        model=model or MODEL,
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
            temperature: float, json_mode: bool = False,
            model: str = "",
            effort: Optional[str] = None) -> tuple[str, int, int]:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("VERTEX_LOCATION", "us-central1"),
    )
    resp = client.models.generate_content(
        model=model or MODEL,
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


# Which argument shape an endpoint accepts, learned from its own error
# messages on the first call. The GPT-5 generation renamed max_tokens to
# max_completion_tokens and rejects a non-default temperature; older
# models and most compatible endpoints do the opposite. Probing beats
# hard-coding a table of model names that goes stale.
#
# Keyed per base_url+model, NOT global: roles can point at two different
# models at once, and one model's answer is not evidence about another's.
_openai_shapes: dict[str, dict[str, Any]] = {}


def _adapt_shape(shape: dict[str, Any], msg: str) -> bool:
    """Read one rejection and drop/flip the parameter it names.

    True if something changed and the call is worth retrying. Endpoints
    disagree about these three and say so in prose, so the message is the
    only honest source — a table of model names goes stale."""
    changed = False
    # "Unsupported parameter: 'max_completion_tokens'" (or the reverse)
    if "max_completion_tokens" in msg or "max_tokens" in msg:
        shape["token_param"] = (
            "max_tokens" if shape["token_param"] == "max_completion_tokens"
            else "max_completion_tokens")
        changed = True
    # "Unsupported value: 'temperature' does not support 0.7"
    if "temperature" in msg and shape["temperature"]:
        shape["temperature"] = False
        changed = True
    # Non-reasoning models and most compatible gateways reject it outright.
    if "reasoning" in msg and shape["effort"]:
        shape["effort"] = False
        changed = True
    return changed


def _usage(resp: Any) -> tuple[int, int]:
    usage = getattr(resp, "usage", None)
    return (getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0)


def _openai(prompt: str, system: Optional[str], max_tokens: int,
            temperature: float, json_mode: bool = False,
            model: str = "",
            effort: Optional[str] = None) -> tuple[str, int, int]:
    """OpenAI and any OpenAI-compatible endpoint.

    Env:
        OPENAI_API_KEY    the key
        OPENAI_BASE_URL   optional; point at a compatible gateway
        LLM_MODEL         the model id, verbatim from the provider's docs
        LLM_EFFORT[_ROLE] reasoning effort, if the model takes one
        LLM_REASONING_FLOOR  retry ceiling when reasoning eats the budget
    """
    from openai import OpenAI

    base_url = os.environ.get("OPENAI_BASE_URL") or None
    model = model or MODEL
    client = OpenAI(base_url=base_url)
    messages = ([{"role": "system", "content": system}] if system else []) \
        + [{"role": "user", "content": prompt}]

    shape = _openai_shapes.setdefault(
        f"{base_url or 'default'}|{model}",
        {"token_param": "max_completion_tokens",
         "temperature": True, "effort": True})

    def once(cap: int):
        """One completion, adapting to whatever the endpoint refuses."""
        for attempt in range(4):
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                shape["token_param"]: cap,
            }
            if shape["temperature"]:
                kwargs["temperature"] = temperature
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if effort and shape["effort"]:
                kwargs["reasoning_effort"] = effort
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as exc:
                if attempt == 3 or not _adapt_shape(shape, str(exc)):
                    raise
        raise RuntimeError("unreachable")

    resp = once(max_tokens)
    choice = resp.choices[0] if resp.choices else None
    text = (getattr(choice.message, "content", "") or "") if choice else ""
    tokens_in, tokens_out = _usage(resp)

    # On a reasoning model the cap covers reasoning tokens too, so a tight
    # cap can be spent entirely on thinking and return nothing at all —
    # silently, with finish_reason "length". Several call sites here are
    # capped at 300-700 because Claude only ever counted visible output.
    # Retry once with room rather than hand a caller an empty string.
    if (not text.strip()
            and getattr(choice, "finish_reason", "") == "length"
            and max_tokens < REASONING_FLOOR):
        log.warning("empty completion from %s at %d tokens — reasoning "
                    "consumed the budget; retrying at %d",
                    model, max_tokens, REASONING_FLOOR)
        resp = once(REASONING_FLOOR)
        choice = resp.choices[0] if resp.choices else None
        text = (getattr(choice.message, "content", "") or "") if choice else ""
        retry_in, retry_out = _usage(resp)
        # Both calls were billed; report both.
        tokens_in += retry_in
        tokens_out += retry_out

    return text, tokens_in, tokens_out
