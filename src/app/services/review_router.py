"""Human-review routing boundary."""

from typing import Protocol, runtime_checkable

from app.agent.decision_context import DecisionContext
from models.decision import DecisionRecord


@runtime_checkable
class ReviewRouter(Protocol):
    async def route(
        self,
        decision: DecisionRecord,
        context: DecisionContext,
    ) -> None:
        """Route a decision requiring human review."""
