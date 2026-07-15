"""Submission adapter interface.

Every tier implements submit() with the same signature; the worker doesn't
care whether the implementation is a deterministic script (Tier 1), an
LLM-driven agent (Tier 2), or a stub that always escalates.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SubmissionOutcome:
    success: bool
    tier: int
    reason: str | None = None
    escalate: bool = False           # True -> go straight to NEEDS_HUMAN, no retry
    screenshots: list[str] = field(default_factory=list)  # Storage paths
    # What was filled/prepared before escalating — the human's checklist:
    # [{"field": label, "value": str|None, "status": "filled"|"needs_you"}]
    fill_sheet: list[dict] = field(default_factory=list)


class SubmissionAdapter(ABC):
    tier: int

    @abstractmethod
    async def submit(
        self,
        *,
        job_url: str,
        profile: dict,
        application: dict,
        posting: dict,
        uid: str,
        app_id: str,
    ) -> SubmissionOutcome:
        ...
