from datetime import UTC, datetime, timedelta

import pytest

from adapters.action_adapter import AdapterUnavailableError
from adapters.email_adapter import MockEmailAdapter
from adapters.llm_provider_adapter import (
    InvalidProviderResponseError,
    QuotaExceededError,
)
from adapters.sms_adapter import MockSMSAdapter
from adapters.task_adapter import MockTaskAdapter
from adapters.timer_adapter import MockTimerAdapter
from app.agent.decision_engine import DecisionEngine
from app.rules.decision_validator import DecisionValidator
from app.services.action_executor import ActionExecutionError, ActionExecutor
from config.settings import Settings
from models.common import EventType
from models.decision import (
    DecisionDisposition,
    ModelDisposition,
    ValidationStatus,
)
from models.event import OpenRequest, RequestType
from tests.integration.agent_support import (
    InMemoryDecisionStore,
    InMemoryRequestStore,
    InMemoryReviewRouter,
    SequenceInterpreter,
    make_action_executor,
    make_context,
    proposal,
)


@pytest.mark.asyncio
async def test_unclear_unrelated_and_provider_failure_never_send_actions() -> None:
    unclear = proposal(
        reason_code="unrelated_message",
        passage="",
        disposition=ModelDisposition.NO_ACTION,
    )
    abstention = proposal(
        reason_code="message_unclear",
        passage="",
        disposition=ModelDisposition.ABSTAIN,
    )
    invalid = proposal(
        reason_code="invalid_stale_proposal",
        passage="When delay risk is high, request an ETA.",
        actions=[
            {
                "action_type": "sms_send",
                "contact_id": "driver-1",
                "payload": {
                    "message": "Unapproved message",
                    "request_type": "eta_request",
                    "operational_step": "eta",
                },
            }
        ],
        load_id="other-load",
    )
    interpreter = SequenceInterpreter(
        [
            unclear,
            abstention,
            invalid,
            InvalidProviderResponseError("invalid response"),
            QuotaExceededError("quota reached"),
        ]
    )
    decisions = InMemoryDecisionStore()
    requests = InMemoryRequestStore()
    review_router = InMemoryReviewRouter()
    executor, ledger, adapters = make_action_executor()
    engine = DecisionEngine(
        interpreter=interpreter,
        validator=DecisionValidator(),
        decision_store=decisions,
        request_store=requests,
        action_executor=executor,
        settings=Settings(),
        review_router=review_router,
    )
    event_times = [datetime.now(UTC) for _ in range(5)]

    outcomes = []
    for index, occurred_at in enumerate(event_times):
        outcomes.append(
            await engine.process(
                make_context(
                    f"event-{index}",
                    EventType.MESSAGE,
                    payload={"text": "unrelated or ambiguous"},
                    occurred_at=occurred_at,
                )
            )
        )

    assert outcomes[0].disposition == DecisionDisposition.NO_ACTION
    assert outcomes[0].validation_status == ValidationStatus.ACCEPTED
    assert outcomes[1].model_disposition == ModelDisposition.ABSTAIN
    assert outcomes[1].review_status.value == "required"
    assert outcomes[1].disposition == DecisionDisposition.NO_ACTION
    assert outcomes[2].validation_status == ValidationStatus.REJECTED
    assert outcomes[2].disposition == DecisionDisposition.NO_ACTION
    assert outcomes[3].validation_status == ValidationStatus.REJECTED
    assert outcomes[3].model_disposition is None
    assert outcomes[4].validation_status == ValidationStatus.PROVIDER_FAILED
    assert outcomes[4].disposition is None
    assert len(decisions.records) == 5
    assert len(ledger.records) == 4
    assert all(action.status.value == "no_action" for _, action in ledger.records)
    assert adapters["sms"].sent == []
    assert adapters["email"].sent == []
    assert adapters["task"].created == []
    assert adapters["timer"].scheduled == []
    assert len(review_router.routed) == 4


@pytest.mark.asyncio
async def test_stale_timer_is_no_action_without_interpreting_or_dispatching() -> None:
    interpreter = SequenceInterpreter([])
    decisions = InMemoryDecisionStore()
    requests = InMemoryRequestStore()
    review_router = InMemoryReviewRouter()
    executor, ledger, adapters = make_action_executor()
    engine = DecisionEngine(
        interpreter=interpreter,
        validator=DecisionValidator(),
        decision_store=decisions,
        request_store=requests,
        action_executor=executor,
        settings=Settings(),
        review_router=review_router,
    )

    record = await engine.process(
        make_context(
            "stale-timer",
            EventType.TIMER,
            payload={"request_id": "already-closed"},
        )
    )

    assert record.disposition == DecisionDisposition.NO_ACTION
    assert record.validation_reasons == ("stale_timer_without_open_request",)
    assert interpreter.requests == []
    assert len(decisions.records) == 1
    assert len(ledger.records) == 1
    assert all(
        adapter_calls == []
        for adapter_calls in (
            adapters["sms"].sent,
            adapters["email"].sent,
            adapters["task"].created,
            adapters["timer"].scheduled,
        )
    )


@pytest.mark.asyncio
async def test_timer_request_is_rechecked_after_llm_returns() -> None:
    class RequestClosesDuringInterpretation(InMemoryRequestStore):
        lookups = 0

        async def get_open_request(
            self, broker_id: str, load_id: str, request_id: str
        ) -> OpenRequest | None:
            self.lookups += 1
            if self.lookups > 1:
                return None
            return await super().get_open_request(
                broker_id,
                load_id,
                request_id,
            )

    now = datetime.now(UTC)
    request = OpenRequest(
        request_id="request-1",
        load_id="load-1",
        request_type=RequestType.ETA,
        question_text="What is your ETA?",
        expires_at=now - timedelta(minutes=1),
    )
    requests = RequestClosesDuringInterpretation()
    requests.requests.append(request)
    interpreter = SequenceInterpreter(
        [
            proposal(
                reason_code="expired_eta_request",
                passage=(
                    "After the follow-up timer expires without an ETA, email "
                    "the dispatcher and create a Hero driver-callback task."
                ),
                actions=[
                    {
                        "action_type": "hero_task",
                        "payload": {
                            "priority": "urgent",
                            "task_type": "driver_callback",
                            "summary": "Call the driver for an ETA.",
                        },
                    }
                ],
            )
        ]
    )
    decisions = InMemoryDecisionStore()
    review_router = InMemoryReviewRouter()
    executor, ledger, adapters = make_action_executor()
    engine = DecisionEngine(
        interpreter=interpreter,
        validator=DecisionValidator(),
        decision_store=decisions,
        request_store=requests,
        action_executor=executor,
        settings=Settings(),
        review_router=review_router,
    )

    record = await engine.process(
        make_context(
            "timer-raced-with-answer",
            EventType.TIMER,
            payload={
                "timer_id": "timer-1",
                "request_id": request.request_id,
            },
            occurred_at=now,
            requests=[request],
        )
    )

    assert record.disposition == DecisionDisposition.NO_ACTION
    assert record.validation_reasons == ("timer_request_no_longer_open",)
    assert requests.lookups == 2
    assert len(ledger.records) == 1
    assert adapters["task"].created == []
    assert review_router.routed == []


@pytest.mark.asyncio
async def test_per_load_call_budget_stops_llm_and_routes_review() -> None:
    interpreter = SequenceInterpreter(
        [
            proposal(
                reason_code="no_action",
                passage="",
                disposition=ModelDisposition.NO_ACTION,
            )
        ]
    )
    decisions = InMemoryDecisionStore()
    requests = InMemoryRequestStore()
    review_router = InMemoryReviewRouter()
    executor, ledger, adapters = make_action_executor()
    engine = DecisionEngine(
        interpreter=interpreter,
        validator=DecisionValidator(),
        decision_store=decisions,
        request_store=requests,
        action_executor=executor,
        settings=Settings(llm_max_calls_per_load=1),
        review_router=review_router,
    )

    first = await engine.process(make_context("budget-1", EventType.PING))
    second = await engine.process(make_context("budget-2", EventType.PING))

    assert first.validation_status == ValidationStatus.ACCEPTED
    assert second.validation_status.value == "budget_exceeded"
    assert second.validation_reasons == ("llm_load_budget_exhausted",)
    assert len(interpreter.requests) == 1
    assert len(review_router.routed) == 1
    assert adapters["sms"].sent == []
    assert len(ledger.records) == 2


@pytest.mark.asyncio
async def test_input_token_limit_skips_reservation_and_provider_call() -> None:
    interpreter = SequenceInterpreter([])
    decisions = InMemoryDecisionStore()
    requests = InMemoryRequestStore()
    review_router = InMemoryReviewRouter()
    executor, ledger, _adapters = make_action_executor()
    engine = DecisionEngine(
        interpreter=interpreter,
        validator=DecisionValidator(),
        decision_store=decisions,
        request_store=requests,
        action_executor=executor,
        settings=Settings(llm_max_input_tokens=1),
        review_router=review_router,
    )

    record = await engine.process(make_context("oversized-input", EventType.PING))

    assert record.validation_status.value == "budget_exceeded"
    assert record.validation_reasons == ("llm_input_token_limit_exceeded",)
    assert interpreter.requests == []
    assert decisions.usage == {}
    assert len(review_router.routed) == 1
    assert len(ledger.records) == 1


@pytest.mark.asyncio
async def test_failed_action_is_reviewed_and_remains_a_failed_proposal() -> None:
    interpreter = SequenceInterpreter(
        [
            proposal(
                reason_code="high_delay_risk",
                passage=(
                    "When delay risk is high, request an ETA from the driver "
                    "by SMS and schedule one follow-up timer."
                ),
                actions=[
                    {
                        "action_type": "sms_send",
                        "contact_id": "driver-1",
                        "payload": {
                            "message": "What is your ETA?",
                            "request_type": "eta_request",
                            "operational_step": "eta",
                        },
                    }
                ],
            )
        ]
    )
    decisions = InMemoryDecisionStore()
    requests = InMemoryRequestStore()
    review_router = InMemoryReviewRouter()
    _executor, ledger, _adapters = make_action_executor()
    failing_executor = ActionExecutor(
        ledger,  # type: ignore[arg-type]
        sms=MockSMSAdapter(AdapterUnavailableError("provider outage")),
        email=MockEmailAdapter(),
        task=MockTaskAdapter(),
        timer=MockTimerAdapter(),
    )
    engine = DecisionEngine(
        interpreter=interpreter,
        validator=DecisionValidator(),
        decision_store=decisions,
        request_store=requests,
        action_executor=failing_executor,
        settings=Settings(),
        review_router=review_router,
    )

    with pytest.raises(ActionExecutionError, match="adapter execution failed"):
        await engine.process(make_context("action-failure", EventType.PING))

    stored = decisions.records[0]
    assert stored.disposition == DecisionDisposition.PROPOSE_ACTION
    assert stored.validation_status == ValidationStatus.ACCEPTED
    assert len(ledger.records) == 1
    assert ledger.updates[0][1]["failure_reason"] == "adapter_unavailable"
    assert len(review_router.routed) == 1
    assert review_router.routed[0].review_status.value == "required"
    assert review_router.routed[0].review_reason == "action_failed:adapter_unavailable"
    assert requests.requests[0].state.value == "cancelled"
