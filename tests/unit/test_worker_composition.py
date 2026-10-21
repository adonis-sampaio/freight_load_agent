from datetime import UTC, datetime

import pytest

from adapters.action_adapter import AdapterAcknowledgement
from adapters.demo_action_adapters import (
    DemoEmailAdapter,
    DemoSMSAdapter,
    DemoTaskAdapter,
    DemoTimerAdapter,
)
from adapters.timer_adapter import TimerRequest
from app.agent.decision_engine import DecisionEngine
from app.rules.decision_validator import DecisionValidator
from app.services.action_executor import ActionExecutor
from config.settings import Settings
from models.action import ActionStatus, ActionType
from models.common import EventType
from models.decision import (
    DecisionDisposition,
    DecisionRecord,
    ModelDisposition,
    ProposedAction,
    ValidationStatus,
)
from tests.integration.agent_support import (
    FakeActionLedger,
    InMemoryDecisionStore,
    InMemoryRequestStore,
    InMemoryReviewRouter,
    make_context,
)
from workers.worker_composition import DemoNoActionInterpreter


@pytest.mark.asyncio
async def test_demo_decision_is_deterministic_and_does_not_reserve_llm_usage() -> None:
    context = make_context("demo-event-1", EventType.PING)
    decision_store = InMemoryDecisionStore()
    action_executor = ActionExecutor(
        FakeActionLedger(),
        sms=DemoSMSAdapter(),
        email=DemoEmailAdapter(),
        task=DemoTaskAdapter(),
        timer=DemoTimerAdapter(),
    )
    engine = DecisionEngine(
        interpreter=DemoNoActionInterpreter(),
        validator=DecisionValidator(),
        decision_store=decision_store,
        request_store=InMemoryRequestStore(),
        action_executor=action_executor,
        settings=Settings(),
        review_router=InMemoryReviewRouter(),
        demo_mode=True,
    )

    decision = await engine.process(context)

    assert decision.disposition == DecisionDisposition.NO_ACTION
    assert decision.model_disposition == ModelDisposition.NO_ACTION
    assert decision.provider_id == "demo"
    assert decision.model_id == "deterministic-no-action"
    assert decision.validation_status == ValidationStatus.ACCEPTED
    assert decision_store.usage == {}


@pytest.mark.asyncio
async def test_demo_adapters_acknowledge_simulation_without_delivery() -> None:
    timestamp = datetime.now(UTC)
    acknowledgements = [
        await DemoSMSAdapter().send("+15551234567", "Demo message"),
        await DemoEmailAdapter().send(
            "thread-1", ["demo@example.com"], "Demo", "Demo email"
        ),
        await DemoTaskAdapter().create("load-1", "normal", "review", "Demo task"),
        await DemoTimerAdapter().schedule(
            TimerRequest(
                load_id="load-1",
                timer_name="demo-timer",
                trigger_at=timestamp,
            )
        ),
    ]

    assert all(
        isinstance(acknowledgement, AdapterAcknowledgement)
        and acknowledgement.accepted
        and acknowledgement.status == "simulated"
        and acknowledgement.external_id.startswith("demo_")
        for acknowledgement in acknowledgements
    )


@pytest.mark.asyncio
async def test_simulated_action_status_is_persisted_without_network_delivery() -> None:
    context = make_context("demo-action-event", EventType.PING)
    ledger = FakeActionLedger()
    executor = ActionExecutor(
        ledger,
        sms=DemoSMSAdapter(),
        email=DemoEmailAdapter(),
        task=DemoTaskAdapter(),
        timer=DemoTimerAdapter(),
    )
    proposed_action = ProposedAction(
        action_type=ActionType.SMS_SEND,
        contact_id="driver-1",
        payload={"message": "Demo only", "operational_step": "eta"},
    )
    decision = DecisionRecord(
        decision_id="demo-decision",
        broker_id=context.broker_id,
        load_id=context.load.load_id,
        trigger_event_id=context.event.event_id,
        disposition=DecisionDisposition.PROPOSE_ACTION,
        model_disposition=ModelDisposition.PROPOSE_ACTION,
        proposed_actions=(proposed_action,),
        validation_status=ValidationStatus.ACCEPTED,
        created_at=datetime.now(UTC),
    )

    result = await executor.execute(decision, context)

    assert result[0].status == ActionStatus.SIMULATED
    assert ledger.updates[0][0][3] == ActionStatus.SIMULATED
