"""Provider-neutral interface for constrained LLM decision proposals."""

import asyncio
from math import ceil
from typing import Protocol, runtime_checkable

import structlog

from config.settings import Settings
from models.decision import LLMDecisionProposal, LLMDecisionRequest

logger = structlog.get_logger(__name__)


class LLMInputLimitExceeded(RuntimeError):
    """Raised when a decision request exceeds the configured input token limit."""


class LLMProviderError(RuntimeError):
    """Base class for explicit provider failures."""


class TransientProviderError(LLMProviderError):
    """A provider error that may succeed on a bounded retry."""


class ProviderTimeoutError(TransientProviderError):
    """The provider did not return within the configured timeout."""


class PermanentProviderError(LLMProviderError):
    """A provider error that must not be retried."""


class QuotaExceededError(PermanentProviderError):
    """The provider account or request quota has been exceeded."""


class BillingError(PermanentProviderError):
    """The provider account cannot make billable requests."""


class InvalidProviderResponseError(PermanentProviderError):
    """The provider returned a response that cannot be normalized."""


@runtime_checkable
class LLMProviderAdapter(Protocol):
    """Provider boundary used by the SOP interpreter."""

    async def propose(
        self, request: LLMDecisionRequest
    ) -> LLMDecisionProposal:
        """Return a normalized proposal for the supplied decision context."""


async def request_proposal(
    adapter: LLMProviderAdapter,
    request: LLMDecisionRequest,
    settings: Settings,
) -> LLMDecisionProposal:
    """Request a proposal with bounded timeout and transient-only retries."""
    retry_count = settings.llm_max_retries
    for attempt in range(retry_count + 1):
        failure: TransientProviderError | None = None
        try:
            return await asyncio.wait_for(
                adapter.propose(request),
                timeout=settings.llm_timeout_seconds,
            )
        except TimeoutError as error:
            failure = ProviderTimeoutError("LLM provider request timed out")
            failure.__cause__ = error
        except TransientProviderError as error:
            failure = error

        if attempt == retry_count:
            logger.error(
                "LLM proposal failed after transient retries",
                attempt_count=attempt + 1,
                failure_type=type(failure).__name__,
            )
            raise failure

        logger.warning(
            "Retrying transient LLM provider failure",
            attempt=attempt + 1,
            failure_type=type(failure).__name__,
        )
        await asyncio.sleep(min(0.25 * (2**attempt), 2.0))

    raise RuntimeError("unreachable provider retry state")


def estimate_input_tokens(request: LLMDecisionRequest) -> int:
    """Estimate tokens conservatively enough for a provider-neutral preflight."""
    return ceil(len(request.model_dump_json()) / 4)
