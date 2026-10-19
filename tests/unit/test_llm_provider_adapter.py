import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from adapters.llm_provider_adapter import (
    BillingError,
    ProviderTimeoutError,
    QuotaExceededError,
    TransientProviderError,
    request_proposal,
)
from config.settings import Settings
from models.action import ActionType
from models.common import EventChannel, EventType, LoadStage
from models.decision import (
    ActiveSOP,
    DecisionEvent,
    DecisionLoadContext,
    LLMDecisionProposal,
    LLMDecisionRequest,
    ModelDisposition,
    SOPSection,
    UncertaintyLevel,
)


def make_request() -> LLMDecisionRequest:
    return LLMDecisionRequest(
        event=DecisionEvent(
            event_id="event-1",
            event_type=EventType.MESSAGE,
            source_channel=EventChannel.SMS,
            payload={"text": "Where is the PO?"},
            occurred_at=datetime.now(UTC),
        ),
        load_context=DecisionLoadContext(
            load_id="load-1",
            current_stage=LoadStage.AT_PICKUP,
        ),
        active_sop=ActiveSOP(
            document_id="standard",
            version="v1",
            content_hash="sha256:abc",
            sections=[SOPSection(section_ref="pickup", text="Request an ETA.")],
        ),
        allowed_action_types=[ActionType.SMS_SEND],
    )


def make_proposal() -> LLMDecisionProposal:
    return LLMDecisionProposal(
        schema_version="1",
        model_disposition=ModelDisposition.NO_ACTION,
        load_id="load-1",
        reason_code="unrelated",
        uncertainty=UncertaintyLevel.LOW,
    )


class SequencedProvider:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def propose(self, _request: LLMDecisionRequest) -> LLMDecisionProposal:
        self.calls += 1
        result = self.outcomes.pop(0)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, asyncio.Event):
            await result.wait()
            return make_proposal()
        return result


@pytest.mark.asyncio
async def test_transient_error_is_retried_then_returns_proposal() -> None:
    provider = SequencedProvider(
        [TransientProviderError("temporary"), make_proposal()]
    )

    result = await request_proposal(
        provider, make_request(), Settings(llm_max_retries=1)
    )

    assert result.model_disposition == ModelDisposition.NO_ACTION
    assert provider.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_error",
    [
        QuotaExceededError("quota"),
        BillingError("billing"),
    ],
)
async def test_permanent_account_errors_are_not_retried(
    provider_error: Exception,
) -> None:
    provider = SequencedProvider([provider_error])

    with pytest.raises(type(provider_error)):
        await request_proposal(
            provider, make_request(), Settings(llm_max_retries=3)
        )

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_timeout_uses_bounded_retry_count() -> None:
    provider = SequencedProvider([asyncio.Event(), asyncio.Event()])

    with pytest.raises(ProviderTimeoutError, match="timed out"):
        await request_proposal(
            provider,
            make_request(),
            Settings(llm_timeout_seconds=0.01, llm_max_retries=1),
        )

    assert provider.calls == 2
