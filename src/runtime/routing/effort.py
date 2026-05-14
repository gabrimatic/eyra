"""Effort estimation wrapper for the deterministic complexity scorer."""

from __future__ import annotations

from chat.complexity_scorer import ComplexityScorer
from runtime.routing.types import EffortEstimate


async def estimate_effort(
    scorer: ComplexityScorer,
    text: str,
    messages: list[dict] | None = None,
) -> EffortEstimate:
    """Return a routing effort estimate for the local policy router."""
    response = await scorer.score_complexity(text, messages=messages)
    return EffortEstimate(
        level=response.classification,
        heuristic_confidence=response.confidence,
        confidence_kind="heuristic_margin",
        reasons=(),
    )
