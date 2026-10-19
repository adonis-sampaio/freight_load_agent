import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest

from adapters.action_adapter import AdapterAcknowledgement
from adapters.email_adapter import MockEmailAdapter
from adapters.sms_adapter import MockSMSAdapter
from adapters.task_adapter import MockTaskAdapter
from adapters.timer_adapter import MockTimerAdapter
from app.agent.decision_context import DecisionContextBuilder
from app.agent.decision_engine import DecisionEngine
from app.rules.decision_validator import DecisionValidator
from app.services.action_executor import ActionExecutor
from config.settings import Settings
from models.action import ActionType
from models.common import EventType
from models.decision import (
    LLMDecisionProposal,
    ModelDisposition,
    UncertaintyLevel,
)
from tests.integration.agent_support import (
    FakeActionLedger,
    InMemoryDecisionStore,
    InMemoryRequestStore,
    InMemoryReviewRouter,
    SequenceInterpreter,
    make_context,
)


class FakeTimeline:
    async def get_recent_events(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


class FakeRequests:
    async def get_open_requests(self, *_args: Any) -> list[Any]:
        return []


class FakeSOPRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def find_applicable_sops(self, **_kwargs: Any) -> list[Any]:
        return self.rows


class FakeSlackAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, load_id: str, payload: dict[str, Any]
    ) -> AdapterAcknowledgement:
        self.sent.append((load_id, payload))
        return AdapterAcknowledgement(
            accepted=True,
            external_id=f"slack-{len(self.sent)}",
            status="sent",
        )


def sop_row(
    *,
    rule_id: str,
    document_id: str,
    scope: str,
    passage: str,
    client_id: str | None,
    load_type: str | None = None,
    event_types: list[str] | None = None,
) -> dict[str, Any]:
    markdown = f"# Broker SOP\n\n## pickup.delay\n{passage}\n"
    return {
        "rule_id": rule_id,
        "document_id": document_id,
        "scope": scope,
        "client_id": client_id,
        "load_type": load_type,
        "load_stage": "at_pickup",
        "section_ref": "pickup.delay",
        "applicable_event_types": event_types or ["ping"],
        "priority": 1,
        "version": f"{rule_id}-v1",
        "effective_from": datetime(2020, 1, 1, tzinfo=UTC),
        "effective_to": None,
        "document_client_id": client_id,
        "chapter_name": "Broker SOP",
        "document_version": f"{document_id}-v1",
        "content_markdown": markdown,
        "content_hash": f"sha256:{hashlib.sha256(markdown.encode()).hexdigest()}",
    }


def proposal_for(
    context: Any,
    passage: str,
    actions: dict[str, Any] | list[dict[str, Any]],
) -> LLMDecisionProposal:
    sop = context.llm_request.active_sop
    return LLMDecisionProposal(
        schema_version="1",
        model_disposition=ModelDisposition.PROPOSE_ACTION,
        load_id=context.load.load_id,
        sop_document_id=sop.document_id,
        sop_version=sop.version,
        sop_content_hash=sop.content_hash,
        sop_section_ref="pickup.delay",
        supporting_passage=passage,
        reason_code="delay_escalation",
        proposed_actions=actions if isinstance(actions, list) else [actions],
        uncertainty=UncertaintyLevel.LOW,
    )


@pytest.mark.asyncio
async def test_client_override_replaces_standard_procedure_and_action() -> None:
    now = datetime.now(UTC)
    standard_text = "For high delay risk, email the dispatcher."
    client_text = "For high delay risk, send a broker alert instead."
    standard = sop_row(
        rule_id="standard-rule",
        document_id="standard-doc",
        scope="standard",
        passage=standard_text,
        client_id=None,
    )
    override = sop_row(
        rule_id="client-rule",
        document_id="client-doc",
        scope="client_override",
        passage=client_text,
        client_id="broker-1",
    )
    rows = [standard, override]
    builder = DecisionContextBuilder(
        FakeTimeline(),  # type: ignore[arg-type]
        FakeRequests(),  # type: ignore[arg-type]
        FakeSOPRepository(rows),  # type: ignore[arg-type]
    )
    standard_base = make_context(
        "standard-event",
        EventType.PING,
        occurred_at=now,
    )
    override_base = make_context(
        "override-event",
        EventType.PING,
        occurred_at=now,
    )
    standard_context = await builder.build(
        "broker-2",
        standard_base.event,
        standard_base.load.model_copy(update={"broker_id": "broker-2"}),
        list(standard_base.contacts),
    )
    override_context = await builder.build(
        "broker-1",
        override_base.event,
        override_base.load,
        list(override_base.contacts),
    )
    interpreter = SequenceInterpreter(
        [
            proposal_for(
                standard_context,
                standard_text,
                {
                    "action_type": "email_send",
                    "contact_id": "dispatcher-1",
                    "payload": {
                        "thread_id": "load-1-main",
                        "to": ["dispatcher@example.com"],
                        "subject": "Delay alert",
                        "body": "The load is delayed.",
                    },
                },
            ),
            proposal_for(
                override_context,
                client_text,
                {
                    "action_type": "slack_alert",
                    "payload": {
                        "channel": "broker-ops",
                        "message": "The load is delayed.",
                    },
                },
            ),
        ]
    )
    decision_store = InMemoryDecisionStore()
    request_store = InMemoryRequestStore()
    review_router = InMemoryReviewRouter()
    ledger = FakeActionLedger()
    email = MockEmailAdapter()
    slack = FakeSlackAdapter()
    executor = ActionExecutor(
        ledger,
        sms=MockSMSAdapter(),
        email=email,
        task=MockTaskAdapter(),
        timer=MockTimerAdapter(),
        additional_adapters={ActionType.SLACK_ALERT: slack},
    )
    engine = DecisionEngine(
        interpreter=interpreter,
        validator=DecisionValidator(),
        decision_store=decision_store,
        request_store=request_store,
        action_executor=executor,
        settings=Settings(),
        review_router=review_router,
    )

    standard_record = await engine.process(standard_context)
    override_record = await engine.process(override_context)

    assert standard_context.active_rule.rule_id == "standard-rule"
    assert override_context.active_rule.rule_id == "client-rule"
    assert standard_record.sop_document_id == "standard-doc"
    assert override_record.sop_document_id == "client-doc"
    assert standard_record.sop_version == "standard-doc-v1"
    assert override_record.sop_version == "client-doc-v1"
    assert len(email.sent) == 1
    assert len(slack.sent) == 1
    assert [action.action_type for _, action in ledger.records] == [
        ActionType.EMAIL_SEND,
        ActionType.SLACK_ALERT,
    ]
    assert review_router.routed == []


@pytest.mark.asyncio
async def test_refrigerated_load_override_requests_temperature_and_seal() -> None:
    now = datetime.now(UTC)
    passage = "For refrigerated loads, request trailer temperature and seal number."
    override = sop_row(
        rule_id="reefer-rule",
        document_id="reefer-doc",
        scope="load_type_override",
        passage=passage,
        client_id="broker-1",
        load_type="refrigerated",
        event_types=["stage_change"],
    )
    builder = DecisionContextBuilder(
        FakeTimeline(),  # type: ignore[arg-type]
        FakeRequests(),  # type: ignore[arg-type]
        FakeSOPRepository([override]),  # type: ignore[arg-type]
    )
    base = make_context("reefer-event", EventType.STAGE_CHANGE, occurred_at=now)
    reefer_load = base.load.model_copy(update={"load_type": "refrigerated"})
    reefer_contacts = list(base.contacts)
    reefer_contacts[0] = reefer_contacts[0].model_copy(
        update={
            "primary_channels": {
                "eta": "sms",
                "temperature": "sms",
                "seal": "sms",
            }
        }
    )
    context = await builder.build(
        "broker-1",
        base.event,
        reefer_load,
        reefer_contacts,
    )
    interpreter = SequenceInterpreter(
        [
            proposal_for(
                context,
                passage,
                [
                    {
                        "action_type": "sms_send",
                        "contact_id": "driver-1",
                        "payload": {
                            "message": "Please send the trailer temperature.",
                            "request_type": "temperature_request",
                            "operational_step": "temperature",
                        },
                    },
                    {
                        "action_type": "sms_send",
                        "contact_id": "driver-1",
                        "payload": {
                            "message": "Please confirm the seal number.",
                            "request_type": "seal_request",
                            "operational_step": "seal",
                        },
                    },
                ],
            )
        ]
    )
    ledger = FakeActionLedger()
    sms = MockSMSAdapter()
    executor = ActionExecutor(
        ledger,
        sms=sms,
        email=MockEmailAdapter(),
        task=MockTaskAdapter(),
        timer=MockTimerAdapter(),
    )
    engine = DecisionEngine(
        interpreter=interpreter,
        validator=DecisionValidator(),
        decision_store=InMemoryDecisionStore(),
        request_store=InMemoryRequestStore(),
        action_executor=executor,
        settings=Settings(),
        review_router=InMemoryReviewRouter(),
    )

    result = await engine.process(context)

    assert result.sop_rule_id == "reefer-rule"
    assert result.validation_status.value == "accepted", result.validation_reasons
    assert [message for _, message in sms.sent] == [
        "Please send the trailer temperature.",
        "Please confirm the seal number.",
    ]
