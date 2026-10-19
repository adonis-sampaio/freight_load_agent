from datetime import UTC, datetime
from typing import Any

import pytest

from adapters.email_adapter import MockEmailAdapter
from adapters.sms_adapter import MockSMSAdapter
from adapters.task_adapter import MockTaskAdapter
from adapters.timer_adapter import MockTimerAdapter
from app.agent.decision_context import DecisionContext
from app.services.action_executor import (
    ActionExecutionError,
    ActionExecutor,
)
from adapters.action_adapter import AdapterUnavailableError
from models.action import ActionStatus
from models.common import EventChannel, EventType
from models.contact import Contact
from models.decision import (
    DecisionDisposition,
    DecisionRecord,
    ModelDisposition,
    ProposedAction,
    ValidationStatus,
)
from models.event import LoadEvent
from models.load import Load
from models.sop import SOPDocument, SOPRule, SOPScope


class FakeLedger:
    def __init__(self) -> None:
        self.records: list[Any] = []
        self.updates: list[tuple[Any, ...]] = []
        self.failure_reasons: list[str | None] = []

    async def record(self, broker_id: str, action: Any) -> str:
        self.records.append((broker_id, action))
        return action.action_id

    async def update_status(
        self, *args: Any, failure_reason: str | None = None
    ) -> None:
        self.updates.append(args)
        self.failure_reasons.append(failure_reason)


def make_context(contact: Contact | None = None) -> DecisionContext:
    now = datetime.now(UTC)
    return DecisionContext(
        broker_id="broker-1",
        event=LoadEvent(
            event_id="event-1",
            load_id="load-1",
            event_type=EventType.PING,
            source_channel=EventChannel.SYSTEM,
            payload_json={},
            occurred_at=now,
            correlation_id="correlation-1",
        ),
        load=Load(
            load_id="load-1",
            broker_id="broker-1",
            broker_name="Broker",
            current_stage="at_pickup",
        ),
        active_rule=SOPRule(
            rule_id="rule-1",
            document_id="standard-1",
            scope=SOPScope.STANDARD,
            section_ref="pickup.delay",
            version="v1",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        active_document=SOPDocument(
            document_id="standard-1",
            chapter_name="Standard",
            version="v1",
            content_markdown="## pickup.delay\nRequest an ETA.",
            content_hash="sha256:abc",
        ),
        contacts=(
            contact
            or Contact(
                contact_id="driver-1",
                load_id="load-1",
                role="driver",
                channels=["sms"],
                primary_channels={"eta": "sms"},
                external_ref="+15551234567",
            ),
        ),
        llm_request={
            "event": {
                "event_id": "event-1",
                "event_type": "ping",
                "source_channel": "system",
                "payload": {},
                "occurred_at": now,
            },
            "load_context": {
                "load_id": "load-1",
                "current_stage": "at_pickup",
            },
            "active_sop": {
                "document_id": "standard-1",
                "version": "v1",
                "content_hash": "sha256:abc",
                "sections": [
                    {
                        "section_ref": "pickup.delay",
                        "text": "Request an ETA.",
                    }
                ],
            },
            "allowed_action_types": ["sms_send"],
        },
    )


def make_decision(
    *,
    disposition: DecisionDisposition = DecisionDisposition.PROPOSE_ACTION,
    validation_status: ValidationStatus = ValidationStatus.ACCEPTED,
    model_disposition: ModelDisposition = ModelDisposition.PROPOSE_ACTION,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id="decision-1",
        broker_id="broker-1",
        load_id="load-1",
        trigger_event_id="event-1",
        sop_rule_id="rule-1",
        disposition=disposition,
        model_disposition=model_disposition,
        proposed_actions=(
            ProposedAction(
                action_type="sms_send",
                contact_id="driver-1",
                payload={
                    "message": "What is your ETA?",
                    "request_type": "eta_request",
                },
            ),
        )
        if model_disposition == ModelDisposition.PROPOSE_ACTION
        else (),
        validation_status=validation_status,
        review_status="required"
        if validation_status == ValidationStatus.REJECTED
        else "not_required",
        review_reason="proposal rejected"
        if validation_status == ValidationStatus.REJECTED
        else None,
        created_at=datetime.now(UTC),
    )


def make_executor(
    ledger: FakeLedger,
    sms: MockSMSAdapter | None = None,
) -> tuple[ActionExecutor, MockSMSAdapter]:
    sms_adapter = sms or MockSMSAdapter()
    return (
        ActionExecutor(
            ledger,  # type: ignore[arg-type]
            sms=sms_adapter,
            email=MockEmailAdapter(),
            task=MockTaskAdapter(),
            timer=MockTimerAdapter(),
        ),
        sms_adapter,
    )


@pytest.mark.asyncio
async def test_accepted_action_is_ledgered_before_send_and_correlated() -> None:
    ledger = FakeLedger()
    executor, sms = make_executor(ledger)

    result = await executor.execute(make_decision(), make_context())

    assert sms.sent == [("+15551234567", "What is your ETA?")]
    assert ledger.records[0][1].status == ActionStatus.QUEUED
    assert ledger.updates[0][3] == ActionStatus.SENT
    assert ledger.updates[0][4] == "sms_1"
    assert result[0].correlation_id == "correlation-1"
    assert result[0].external_id == "sms_1"


@pytest.mark.asyncio
async def test_no_action_decision_is_logged_without_sending() -> None:
    ledger = FakeLedger()
    executor, sms = make_executor(ledger)
    decision = make_decision(
        disposition=DecisionDisposition.NO_ACTION,
        validation_status=ValidationStatus.REJECTED,
    )

    result = await executor.execute(decision, make_context())

    assert result[0].status == ActionStatus.NO_ACTION
    assert ledger.records[0][1].action_type == "status_update"
    assert sms.sent == []


@pytest.mark.asyncio
async def test_execution_revalidates_current_contact_before_sending() -> None:
    ledger = FakeLedger()
    executor, sms = make_executor(ledger)
    changed_contact = Contact(
        contact_id="driver-1",
        load_id="load-1",
        role="driver",
        channels=["sms"],
        primary_channels={"general": "sms"},
        external_ref="+15551234567",
    )

    with pytest.raises(ActionExecutionError, match="contact validation failed"):
        await executor.execute(make_decision(), make_context(changed_contact))

    assert sms.sent == []
    assert ledger.updates[0][3] == ActionStatus.FAILED
    assert ledger.failure_reasons[0] == "invalid_contact:contact_primary_channel_mismatch"


@pytest.mark.asyncio
async def test_adapter_failure_is_persisted_and_surfaced() -> None:
    ledger = FakeLedger()
    failing_sms = MockSMSAdapter(
        AdapterUnavailableError("SMS service unavailable")
    )
    executor, sms = make_executor(ledger, failing_sms)

    with pytest.raises(ActionExecutionError, match="adapter execution failed"):
        await executor.execute(make_decision(), make_context())

    assert sms.sent == []
    assert ledger.updates[0][3] == ActionStatus.FAILED
    assert ledger.failure_reasons[0] == "adapter_unavailable"
