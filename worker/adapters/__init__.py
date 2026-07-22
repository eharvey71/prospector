"""Adapter registry. get_adapter() returns None for ATSes with no adapter,
which the worker turns into a NEEDS_HUMAN escalation."""
from __future__ import annotations

from schemas import AtsType

from .agentic import AgenticAdapter
from .base import SubmissionAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter

_REGISTRY: dict[AtsType, SubmissionAdapter] = {
    AtsType.GREENHOUSE: GreenhouseAdapter(),
    AtsType.LEVER: LeverAdapter(),
    # Public-form ATSes without a deterministic adapter yet: Tier 2 discovers
    # the form shape per-page (LLM plans, code fills+verifies).
    AtsType.ASHBY: AgenticAdapter(),
    AtsType.SMARTRECRUITERS: AgenticAdapter(),
    AtsType.WORKABLE: AgenticAdapter(),
    AtsType.UNKNOWN: AgenticAdapter(),
}


def get_adapter(ats: AtsType) -> SubmissionAdapter | None:
    return _REGISTRY.get(ats)
