"""Provider-agnostic LLM access.

One function: generate(). Provider and model come from env vars, so switching
Vertex <-> Anthropic <-> anything-OpenAI-compatible is a config change.

Env:
    LLM_PROVIDER   anthropic | vertex          (default: anthropic)
    LLM_MODEL      provider-specific model id
    ANTHROPIC_API_KEY / GOOGLE_CLOUD_PROJECT   as appropriate
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic")
MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
) -> str:
    """Single-turn text generation."""
    if PROVIDER == "anthropic":
        return _anthropic(prompt, system, max_tokens, temperature)
    if PROVIDER == "vertex":
        return _vertex(prompt, system, max_tokens, temperature)
    raise ValueError(f"Unknown LLM_PROVIDER: {PROVIDER}")


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
    raw = generate(prompt, system=full_system, max_tokens=max_tokens, temperature=0.2)
    try:
        return schema.model_validate_json(_strip_fences(raw))
    except Exception:
        repair = generate(
            f"The following was supposed to be valid JSON for the schema but "
            f"failed to parse. Output ONLY the corrected JSON.\n\n{raw}",
            system=full_system,
            max_tokens=max_tokens,
            temperature=0.0,
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

def _anthropic(prompt: str, system: Optional[str], max_tokens: int, temperature: float) -> str:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    kwargs: dict[str, Any] = dict(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    return "".join(block.text for block in resp.content if block.type == "text")


def _vertex(prompt: str, system: Optional[str], max_tokens: int, temperature: float) -> str:
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
    return resp.text or ""
