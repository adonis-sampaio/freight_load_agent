"""Shared result and error types for external action adapters."""

from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from models.common import StrictModel


class AdapterAcknowledgement(StrictModel):
    accepted: bool
    external_id: str = Field(min_length=1)
    status: str = Field(min_length=1)


class AdapterError(RuntimeError):
    """Raised when an external action cannot be completed."""


class AdapterUnavailableError(AdapterError):
    """The target integration is unavailable."""


class AdapterRejectedError(AdapterError):
    """The target integration rejected the action."""


@runtime_checkable
class GenericActionAdapter(Protocol):
    async def execute(
        self, load_id: str, payload: dict[str, Any]
    ) -> AdapterAcknowledgement: ...
