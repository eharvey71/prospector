"""Adapter registry. get_adapter() returns None for ATSes with no adapter,
which the worker turns into a NEEDS_HUMAN escalation."""
from __future__ import annotations

from schemas import AtsType

from .base import SubmissionAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter

_REGISTRY: dict[AtsType, SubmissionAdapter] = {
    AtsType.GREENHOUSE: GreenhouseAdapter(),
    AtsType.LEVER: LeverAdapter(),
    # AtsType.UNKNOWN: AgenticAdapter(),  # Tier 2, LLM-driven, later
}


def get_adapter(ats: AtsType) -> SubmissionAdapter | None:
    return _REGISTRY.get(ats)
