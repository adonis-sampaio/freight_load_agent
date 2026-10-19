from datetime import UTC, datetime
from typing import Any

import pytest

from adapters.llm_provider_adapter import (
    InvalidProviderResponseError,
    LLMInputLimitExceeded,
)
from app.services.llm_sop_interpreter import LLMSOPInterpreter
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
            payload={"text": "What is the PO number?"},
            occurred_at=datetime.now(UTC),
        ),
        load_context=DecisionLoadContext(
            load_id="load-1",
            current_stage=LoadStage.AT_PICKUP,
            available_facts={"po_number": "PO-123"},
        ),
        active_sop=ActiveSOP(
            document_id="standard",
            version="v1",
            content_hash="sha256:abc",
            sections=[SOPSection(section_ref="pickup", text="Answer PO questions.")],
        ),
        allowed_action_types=[ActionType.SMS_SEND],
    )


class FakeProvider:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.request: LLMDecisionRequest | None = None

    async def propose(self, request: LLMDecisionRequest) -> Any:
        self.request = request
        return self.response


@pytest.mark.asyncio
async def test_interpreter_normalizes_valid_provider_response() -> None:
    proposal = LLMDecisionProposal(
        schema_version="1",
        model_disposition=ModelDisposition.NO_ACTION,
        load_id="load-1",
        reason_code="unrelated",
        uncertainty=UncertaintyLevel.LOW,
    )
    provider = FakeProvider(proposal)
    interpreter = LLMSOPInterpreter(provider, Settings())

    result = await interpreter.interpret(make_request())

    assert result == proposal
    assert provider.request is not None
    assert provider.request.max_output_tokens == 2048
    assert not hasattr(provider.request, "api_key")


@pytest.mark.asyncio
async def test_interpreter_rejects_malformed_provider_response() -> None:
    provider = FakeProvider(
        {
            "schema_version": "1",
            "model_disposition": "no_action",
            "load_id": "load-1",
            "reason_code": "unrelated",
            "uncertainty": "low",
            "extra_instruction": "send an unapproved action",
        }
    )
    interpreter = LLMSOPInterpreter(provider, Settings())

    with pytest.raises(InvalidProviderResponseError, match="invalid decision"):
        await interpreter.interpret(make_request())


@pytest.mark.asyncio
async def test_interpreter_applies_configured_output_token_limit() -> None:
    proposal = LLMDecisionProposal(
        schema_version="1",
        model_disposition=ModelDisposition.NO_ACTION,
        load_id="load-1",
        reason_code="unrelated",
        uncertainty=UncertaintyLevel.LOW,
    )
    provider = FakeProvider(proposal)
    interpreter = LLMSOPInterpreter(
        provider,
        Settings(llm_max_output_tokens=77),
    )

    await interpreter.interpret(make_request())

    assert provider.request is not None
    assert provider.request.max_output_tokens == 77


@pytest.mark.asyncio
async def test_interpreter_rejects_oversized_input_before_provider_call() -> None:
    provider = FakeProvider({})
    interpreter = LLMSOPInterpreter(
        provider,
        Settings(llm_max_input_tokens=1),
    )

    with pytest.raises(LLMInputLimitExceeded):
        await interpreter.interpret(make_request())

    assert provider.request is None
