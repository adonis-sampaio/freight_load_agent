"""Reusable in-memory dependencies for agent integration scenarios."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters.email_adapter import MockEmailAdapter
from adapters.sms_adapter import MockSMSAdapter
from adapters.task_adapter import MockTaskAdapter
from adapters.timer_adapter import MockTimerAdapter
from app.agent.decision_context import (
    DecisionContext,
    extract_markdown_section,
)
from app.memory.action_ledger import ActionLedger
from app.memory.request_store import is_usable_eta
from app.services.action_executor import ActionExecutor
from models.action import ActionType
from models.common import EventChannel, EventType
from models.contact import Contact
from models.decision import (
    ActiveSOP,
    DecisionEvent,
    DecisionLoadContext,
    LLMDecisionProposal,
    LLMDecisionRequest,
    ModelDisposition,
    ProposedAction,
    SOPSection,
    UncertaintyLevel,
)
from models.event import LoadEvent, OpenRequest, RequestState
from models.load import Load
from models.sop import SOPDocument, SOPRule, SOPScope

FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "sops" / "standard.md"
)
SOP_MARKDOWN = FIXTURE_PATH.read_text()
SOP_HASH = f"sha256:{hashlib.sha256(SOP_MARKDOWN.encode()).hexdigest()}"
SOP_PASSAGE = extract_markdown_section(SOP_MARKDOWN, "pickup.delay")


class InMemoryDecisionStore:
    def __init__(self) -> None:
        self.records = []
        self.usage: dict[tuple[str, str], dict[str, float | int]] = {}

    async def record(self, decision: Any) -> str:
        self.records.append(decision)
        return decision.decision_id

    async def reserve_llm_call(
        self,
        broker_id: str,
        load_id: str,
        *,
        input_tokens: int,
        reserved_output_tokens: int,
        reserved_cost_usd: float,
        maximum_calls: int,
        maximum_cost_usd: float,
    ) -> int:
        from app.memory.decision_store import LLMUsageBudgetExceededError

        key = (broker_id, load_id)
        current = self.usage.get(
            key,
            {
                "call_count": 0,
                "reserved_cost_usd": 0.0,
                "estimated_input_tokens": 0,
                "reserved_output_tokens": 0,
                "total_latency_ms": 0,
            },
        )
        if (
            current["call_count"] >= maximum_calls
            or current["reserved_cost_usd"] + reserved_cost_usd
            > maximum_cost_usd
        ):
            raise LLMUsageBudgetExceededError("budget exhausted")
        updated = {
            **current,
            "call_count": current["call_count"] + 1,
            "reserved_cost_usd": current["reserved_cost_usd"] + reserved_cost_usd,
            "estimated_input_tokens": (
                current["estimated_input_tokens"] + input_tokens
            ),
            "reserved_output_tokens": (
                current["reserved_output_tokens"] + reserved_output_tokens
            ),
        }
        self.usage[key] = updated
        return updated["call_count"]

    async def record_llm_latency(
        self, broker_id: str, load_id: str, latency_ms: int
    ) -> None:
        key = (broker_id, load_id)
        current = self.usage[key]
        self.usage[key] = {
            **current,
            "last_latency_ms": latency_ms,
            "total_latency_ms": current["total_latency_ms"] + latency_ms,
        }


class InMemoryRequestStore:
    def __init__(self) -> None:
        self.requests: list[OpenRequest] = []

    async def create(self, _broker_id: str, request: OpenRequest) -> str:
        self.requests.append(request)
        return request.request_id

    async def increment_reminder(
        self, _broker_id: str, _load_id: str, request_id: str, **_kwargs: Any
    ) -> int:
        for index, request in enumerate(self.requests):
            if request.request_id == request_id and request.state == RequestState.OPEN:
                updated = request.model_copy(
                    update={
                        "reminder_count": request.reminder_count + 1,
                        "last_prompted_at": datetime.now(UTC),
                    }
                )
                self.requests[index] = updated
                return updated.reminder_count
        raise LookupError(request_id)

    async def transition(
        self,
        _broker_id: str,
        _load_id: str,
        request_id: str,
        new_state: RequestState,
    ) -> None:
        for index, request in enumerate(self.requests):
            if request.request_id == request_id and request.state == RequestState.OPEN:
                self.requests[index] = request.model_copy(
                    update={
                        "state": new_state,
                        "resolved_at": datetime.now(UTC),
                    }
                )
                return
        raise LookupError(request_id)

    async def get_open_request(
        self, _broker_id: str, _load_id: str, request_id: str
    ) -> OpenRequest | None:
        return next(
            (
                request
                for request in self.requests
                if request.request_id == request_id
                and request.state == RequestState.OPEN
            ),
            None,
        )

    async def resolve_answer(
        self,
        broker_id: str,
        load_id: str,
        request_id: str,
        answer: str,
        *,
        explicitly_answers_question: bool = False,
    ) -> bool:
        request = await self.get_open_request(broker_id, load_id, request_id)
        if request is None:
            return False
        if not (is_usable_eta(answer) or explicitly_answers_question):
            return False
        await self.transition(
            broker_id,
            load_id,
            request_id,
            RequestState.ANSWERED,
        )
        return True

    def current_open(self, load_id: str) -> list[OpenRequest]:
        return [
            request
            for request in self.requests
            if request.load_id == load_id and request.state == RequestState.OPEN
        ]


class FakeActionLedger(ActionLedger):
    def __init__(self) -> None:
        self.records = []
        self.updates = []

    async def record(self, broker_id: str, action: Any) -> str:
        self.records.append((broker_id, action))
        return action.action_id

    async def update_status(self, *args: Any, **kwargs: Any) -> None:
        self.updates.append((args, kwargs))


class SequenceInterpreter:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[LLMDecisionRequest] = []

    async def interpret(self, request: LLMDecisionRequest) -> LLMDecisionProposal:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class InMemoryReviewRouter:
    def __init__(self) -> None:
        self.routed = []

    async def route(self, decision: Any, _context: DecisionContext) -> None:
        self.routed.append(decision)


def make_contacts() -> tuple[Contact, Contact]:
    return (
        Contact(
            contact_id="driver-1",
            load_id="load-1",
            role="driver",
            channels=["sms"],
            primary_channels={"eta": "sms"},
            external_ref="+15551234567",
        ),
        Contact(
            contact_id="dispatcher-1",
            load_id="load-1",
            role="dispatcher",
            channels=["email"],
            primary_channels={"escalation": "email"},
            external_ref="dispatcher@example.com",
        ),
    )


def make_context(
    event_id: str,
    event_type: EventType,
    *,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    requests: list[OpenRequest] | None = None,
) -> DecisionContext:
    now = occurred_at or datetime.now(UTC)
    event = LoadEvent(
        event_id=event_id,
        load_id="load-1",
        event_type=event_type,
        source_channel=(
            EventChannel.INTERNAL_TIMER
            if event_type == EventType.TIMER
            else EventChannel.SMS
            if event_type == EventType.MESSAGE
            else EventChannel.SYSTEM
        ),
        source_contact_id="driver-1" if event_type == EventType.MESSAGE else None,
        payload_json=payload or {},
        occurred_at=now,
        correlation_id=f"correlation-{event_id}",
    )
    load = Load(
        load_id="load-1",
        broker_id="broker-1",
        broker_name="Broker",
        current_stage="at_pickup",
        available_facts={"po_number": "PO-123"},
    )
    sop_document = SOPDocument(
        document_id="standard-1",
        chapter_name="Standard Freight Operations",
        version="v1",
        content_markdown=SOP_MARKDOWN,
        content_hash=SOP_HASH,
    )
    sop_rule = SOPRule(
        rule_id="rule-pickup-delay",
        document_id="standard-1",
        scope=SOPScope.STANDARD,
        load_stage="at_pickup",
        section_ref="pickup.delay",
        applicable_event_types=[
            EventType.PING,
            EventType.MESSAGE,
            EventType.TIMER,
        ],
        version="v1",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    event_context = DecisionEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        source_channel=event.source_channel,
        source_contact_id=event.source_contact_id,
        payload=event.payload_json,
        occurred_at=event.occurred_at,
    )
    request_context = DecisionLoadContext(
        load_id=load.load_id,
        current_stage=load.current_stage,
        available_facts=load.available_facts,
        open_requests=[
            {
                "request_id": request.request_id,
                "request_type": request.request_type,
                "state": "open",
            }
            for request in (requests or [])
            if request.state == RequestState.OPEN
        ],
    )
    llm_request = LLMDecisionRequest(
        event=event_context,
        load_context=request_context,
        active_sop=ActiveSOP(
            document_id=sop_document.document_id,
            version=sop_document.version,
            content_hash=sop_document.content_hash,
            sections=[SOPSection(section_ref="pickup.delay", text=SOP_PASSAGE)],
        ),
        allowed_action_types=list(ActionType),
    )
    return DecisionContext(
        broker_id="broker-1",
        event=event,
        load=load,
        active_rule=sop_rule,
        active_document=sop_document,
        open_requests=tuple(requests or []),
        contacts=make_contacts(),
        llm_request=llm_request,
    )


def proposal(
    *,
    reason_code: str,
    passage: str,
    actions: list[dict[str, Any]] | None = None,
    disposition: ModelDisposition = ModelDisposition.PROPOSE_ACTION,
    load_id: str = "load-1",
    uncertainty: UncertaintyLevel = UncertaintyLevel.LOW,
) -> LLMDecisionProposal:
    action_models = [
        ProposedAction.model_validate(action) for action in (actions or [])
    ]
    return LLMDecisionProposal(
        schema_version="1",
        model_disposition=disposition,
        load_id=load_id,
        sop_document_id="standard-1",
        sop_version="v1",
        sop_content_hash=SOP_HASH,
        sop_section_ref="pickup.delay",
        supporting_passage=passage,
        reason_code=reason_code,
        proposed_actions=action_models,
        uncertainty=uncertainty,
    )


def make_action_executor() -> tuple[ActionExecutor, FakeActionLedger, dict[str, Any]]:
    ledger = FakeActionLedger()
    sms = MockSMSAdapter()
    email = MockEmailAdapter()
    task = MockTaskAdapter()
    timer = MockTimerAdapter()
    executor = ActionExecutor(
        ledger,
        sms=sms,
        email=email,
        task=task,
        timer=timer,
    )
    return executor, ledger, {
        "sms": sms,
        "email": email,
        "task": task,
        "timer": timer,
    }
