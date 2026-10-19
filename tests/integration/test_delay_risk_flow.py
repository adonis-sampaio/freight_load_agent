from datetime import UTC, datetime, timedelta

import pytest

from app.agent.decision_engine import DecisionEngine
from app.rules.decision_validator import DecisionValidator
from config.settings import Settings
from models.common import EventType
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
async def test_high_delay_risk_clarification_timers_and_final_escalation() -> None:
    now = datetime.now(UTC)
    request_passage = (
        "When delay risk is high, request an ETA from the driver by SMS and "
        "schedule one follow-up timer."
    )
    clarification_passage = (
        "A driver question that does not include an ETA does not resolve the "
        "ETA request; answer from available load facts and keep the request open."
    )
    escalation_passage = (
        "After the follow-up timer expires without an ETA, email the dispatcher "
        "and create a Hero driver-callback task."
    )
    proposals = [
        proposal(
            reason_code="high_delay_risk",
            passage=request_passage,
            actions=[
                {
                    "action_type": "sms_send",
                    "contact_id": "driver-1",
                    "payload": {
                        "message": "What is your ETA?",
                        "request_type": "eta_request",
                        "operational_step": "eta",
                    },
                },
                {
                    "action_type": "timer_set",
                    "payload": {
                        "timer_name": "eta_followup_1",
                        "trigger_at": (now + timedelta(minutes=30)).isoformat(),
                        "context": {},
                    },
                },
            ],
        ),
        proposal(
            reason_code="answers_load_metadata_not_eta",
            passage=clarification_passage,
            actions=[
                {
                    "action_type": "sms_send",
                    "contact_id": "driver-1",
                    "payload": {
                        "message": "The PO number is PO-123.",
                        "operational_step": "eta",
                    },
                }
            ],
        ),
        proposal(
            reason_code="first_eta_reminder",
            passage=request_passage,
            actions=[
                {
                    "action_type": "sms_send",
                    "contact_id": "driver-1",
                    "payload": {
                        "message": "Please send your ETA for pickup.",
                        "request_type": "eta_request",
                        "operational_step": "eta",
                    },
                },
                {
                    "action_type": "timer_set",
                    "payload": {
                        "timer_name": "eta_followup_final",
                        "trigger_at": (now + timedelta(minutes=60)).isoformat(),
                        "context": {},
                    },
                },
            ],
        ),
        proposal(
            reason_code="eta_unanswered_final_escalation",
            passage=escalation_passage,
            actions=[
                {
                    "action_type": "email_send",
                    "contact_id": "dispatcher-1",
                    "payload": {
                        "thread_id": "load-1-main",
                        "to": ["dispatcher@example.com"],
                        "subject": "ETA required for load load-1",
                        "body": "The driver has not provided an ETA.",
                        "operational_step": "escalation",
                    },
                },
                {
                    "action_type": "hero_task",
                    "payload": {
                        "priority": "urgent",
                        "task_type": "driver_callback",
                        "summary": "Call the driver for an ETA.",
                    },
                },
            ],
        ),
    ]
    interpreter = SequenceInterpreter(proposals)
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
        settings=Settings(eta_request_timeout_seconds=1800),
        review_router=review_router,
    )

    records = [
        await engine.process(
            make_context("event-risk-high", EventType.PING, occurred_at=now)
        ),
        await engine.process(
            make_context(
                "event-driver-question",
                EventType.MESSAGE,
                payload={"text": "What's the PO number?"},
                occurred_at=now + timedelta(minutes=1),
                requests=requests.current_open("load-1"),
            )
        ),
        await engine.process(
            make_context(
                "event-first-timer",
                EventType.TIMER,
                payload={
                    "timer_id": "timer_1",
                    "request_id": requests.requests[0].request_id,
                },
                occurred_at=now + timedelta(minutes=30),
                requests=requests.current_open("load-1"),
            )
        ),
        await engine.process(
            make_context(
                "event-final-timer",
                EventType.TIMER,
                payload={
                    "timer_id": "timer_2",
                    "request_id": requests.requests[0].request_id,
                },
                occurred_at=now + timedelta(minutes=60),
                requests=requests.current_open("load-1"),
            )
        ),
    ]

    assert len(records) == 4
    assert all(record.validation_status.value == "accepted" for record in records)
    assert requests.requests[0].state.value == "expired"
    assert requests.requests[0].reminder_count == 1
    assert len(ledger.records) == 7
    assert all(
        action.trigger_event_id in {
            "event-risk-high",
            "event-driver-question",
            "event-first-timer",
            "event-final-timer",
        }
        and action.sop_rule_id == "rule-pickup-delay"
        for _, action in ledger.records
    )
    assert adapters["sms"].sent[0][1] == "What is your ETA?"
    assert len(adapters["sms"].sent) == 3
    assert len(adapters["email"].sent) == 1
    assert len(adapters["task"].created) == 1
    assert adapters["timer"].scheduled[0].context["request_id"] == (
        requests.requests[0].request_id
    )
    assert len(decisions.records) == 4
    assert all(record.sop_content_hash for record in decisions.records)
    assert review_router.routed == []
