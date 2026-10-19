"""Decision orchestration for fail-closed SOP interpretation."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import logging
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from adapters.llm_provider_adapter import (
    InvalidProviderResponseError,
    LLMInputLimitExceeded,
    LLMProviderError,
    estimate_input_tokens,
)
from app.agent.decision_context import DecisionContext
from app.memory.decision_store import (
    DecisionStore,
    LLMUsageBudgetExceededError,
)
from app.memory.request_store import RequestStore, is_usable_eta
from app.rules.decision_validator import DecisionValidationResult, DecisionValidator
from app.services.action_executor import ActionExecutionError, ActionExecutor
from app.services.llm_sop_interpreter import LLMSOPInterpreter
from app.services.review_router import ReviewRouter
from config.settings import Settings
from models.action import ActionType
from models.common import EventType
from models.decision import (
    DecisionDisposition,
    DecisionRecord,
    LLMDecisionProposal,
    LLMDecisionRequest,
    ProposedAction,
    ReviewStatus,
    ValidationStatus,
)
from models.event import LoadEvent, OpenRequest, RequestState, RequestType
from models.load import Load

logger = logging.getLogger(__name__)


class DecisionInterpreter(Protocol):
    async def interpret(
        self, request: LLMDecisionRequest
    ) -> LLMDecisionProposal: ...


class DecisionEngine:
    def __init__(
        self,
        *,
        interpreter: LLMSOPInterpreter | DecisionInterpreter,
        validator: DecisionValidator,
        decision_store: DecisionStore,
        request_store: RequestStore,
        action_executor: ActionExecutor,
        settings: Settings,
        review_router: ReviewRouter,
    ) -> None:
        self._interpreter = interpreter
        self._validator = validator
        self._decision_store = decision_store
        self._request_store = request_store
        self._action_executor = action_executor
        self._settings = settings
        self._review_router = review_router

    async def process(self, context: DecisionContext) -> DecisionRecord:
        timer_request = await self._open_timer_request(context)
        if context.event.event_type == EventType.TIMER and timer_request is None:
            record = self._system_no_action(
                context,
                reason="stale_timer_without_open_request",
            )
            await self._decision_store.record(record)
            await self._action_executor.execute(record, context)
            return record

        input_tokens = estimate_input_tokens(context.llm_request)
        estimated_cost = self._settings.llm_reserved_cost_per_call_usd
        if input_tokens > self._settings.llm_max_input_tokens:
            return await self._record_budget_exceeded(
                context,
                reason="llm_input_token_limit_exceeded",
            )
        try:
            await self._decision_store.reserve_llm_call(
                context.broker_id,
                context.load.load_id,
                input_tokens=input_tokens,
                reserved_output_tokens=self._settings.llm_max_output_tokens,
                reserved_cost_usd=estimated_cost,
                maximum_calls=self._settings.llm_max_calls_per_load,
                maximum_cost_usd=self._settings.llm_max_cost_per_load_usd,
            )
        except LLMUsageBudgetExceededError:
            return await self._record_budget_exceeded(
                context,
                reason="llm_load_budget_exhausted",
            )

        started = perf_counter()
        try:
            proposal = await self._interpreter.interpret(context.llm_request)
        except LLMInputLimitExceeded:
            latency_ms = _elapsed_ms(started)
            await self._record_llm_latency(
                context,
                input_tokens,
                estimated_cost,
                latency_ms,
            )
            return await self._record_budget_exceeded(
                context,
                reason="llm_input_token_limit_exceeded",
            )
        except InvalidProviderResponseError:
            latency_ms = _elapsed_ms(started)
            await self._record_llm_latency(
                context,
                input_tokens,
                estimated_cost,
                latency_ms,
            )
            record = self._failed_validation_record(
                context,
                reason="invalid_provider_response",
            )
            await self._decision_store.record(record)
            await self._action_executor.execute(record, context)
            await self._review_router.route(record, context)
            return record
        except LLMProviderError as error:
            latency_ms = _elapsed_ms(started)
            await self._record_llm_latency(
                context,
                input_tokens,
                estimated_cost,
                latency_ms,
            )
            record = self._provider_failure_record(
                context,
                error_type=type(error).__name__,
            )
            await self._decision_store.record(record)
            await self._review_router.route(record, context)
            return record

        latency_ms = _elapsed_ms(started)
        await self._record_llm_latency(
            context,
            input_tokens,
            estimated_cost,
            latency_ms,
        )
        validation = self._validator.validate(proposal, context)
        record = self._decision_record(context, proposal, validation)

        if (
            timer_request is not None
            and validation.disposition == DecisionDisposition.PROPOSE_ACTION
            and not await self._timer_request_is_current(context, timer_request)
        ):
            record = self._system_no_action(
                context,
                reason="timer_request_no_longer_open",
            )
            await self._decision_store.record(record)
            await self._action_executor.execute(record, context)
            return record

        await self._decision_store.record(record)

        if validation.disposition == DecisionDisposition.NO_ACTION:
            await self._action_executor.execute(record, context)
            if validation.review_status == ReviewStatus.REQUIRED:
                await self._review_router.route(record, context)
            await self._resolve_usable_eta(context, proposal.reason_code)
            return record

        executable_actions, created_request_id = (
            await self._prepare_request_actions(validation.proposed_actions, context)
        )
        action_succeeded = False
        try:
            await self._action_executor.execute(
                record,
                context,
                actions=executable_actions,
            )
            action_succeeded = True
        except ActionExecutionError as error:
            await self._review_router.route(
                record.model_copy(
                    update={
                        "review_status": ReviewStatus.REQUIRED,
                        "review_reason": f"action_failed:{error.reason_code}",
                    }
                ),
                context,
            )
            raise
        finally:
            if not action_succeeded and created_request_id is not None:
                await self._request_store.transition(
                    context.broker_id,
                    context.load.load_id,
                    created_request_id,
                    RequestState.CANCELLED,
                )

        if timer_request is not None:
            sent_eta_reminder = any(
                action.action_type == ActionType.SMS_SEND
                and action.payload.get("request_type") == RequestType.ETA.value
                for action in executable_actions
            )
            if sent_eta_reminder:
                await self._request_store.increment_reminder(
                    context.broker_id,
                    context.load.load_id,
                    timer_request.request_id,
                    maximum_reminders=self._settings.max_eta_reminders,
                )
            else:
                await self._request_store.transition(
                    context.broker_id,
                    context.load.load_id,
                    timer_request.request_id,
                    RequestState.EXPIRED,
                )
        else:
            await self._resolve_usable_eta(context, proposal.reason_code)
        return record

    async def _record_budget_exceeded(
        self,
        context: DecisionContext,
        *,
        reason: str,
    ) -> DecisionRecord:
        record = DecisionRecord(
            decision_id=f"dec_{uuid4().hex}",
            broker_id=context.broker_id,
            load_id=context.load.load_id,
            trigger_event_id=context.event.event_id,
            sop_rule_id=context.active_rule.rule_id,
            disposition=DecisionDisposition.NO_ACTION,
            review_status=ReviewStatus.REQUIRED,
            review_reason=reason,
            sop_document_id=context.active_document.document_id,
            sop_version=context.active_document.version,
            sop_content_hash=context.active_document.content_hash,
            ruleset_version=context.active_rule.version,
            validation_status=ValidationStatus.BUDGET_EXCEEDED,
            validation_reasons=(reason,),
            created_at=datetime.now(UTC),
        )
        await self._decision_store.record(record)
        await self._action_executor.execute(record, context)
        await self._review_router.route(record, context)
        return record

    async def _record_llm_latency(
        self,
        context: DecisionContext,
        input_tokens: int,
        estimated_cost: float,
        latency_ms: int,
    ) -> None:
        await self._decision_store.record_llm_latency(
            context.broker_id,
            context.load.load_id,
            latency_ms,
        )
        logger.info(
            "llm_decision_metrics",
            extra={
                "broker_id": context.broker_id,
                "load_id": context.load.load_id,
                "event_id": context.event.event_id,
                "estimated_input_tokens": input_tokens,
                "reserved_output_tokens": self._settings.llm_max_output_tokens,
                "estimated_cost_usd": estimated_cost,
                "latency_ms": latency_ms,
            },
        )

    async def record_no_applicable_sop(
        self,
        broker_id: str,
        event: LoadEvent,
        load: Load,
    ) -> DecisionRecord:
        if event.load_id != load.load_id or broker_id != load.broker_id:
            raise ValueError("event and broker must match the supplied load")
        record = DecisionRecord(
            decision_id=f"dec_{uuid4().hex}",
            broker_id=broker_id,
            load_id=load.load_id,
            trigger_event_id=event.event_id,
            disposition=DecisionDisposition.NO_ACTION,
            validation_status=ValidationStatus.ACCEPTED,
            validation_reasons=("no_applicable_sop",),
            created_at=datetime.now(UTC),
        )
        await self._decision_store.record(record)
        await self._action_executor.record_no_action(broker_id, event)
        return record

    def _decision_record(
        self,
        context: DecisionContext,
        proposal: LLMDecisionProposal,
        validation: DecisionValidationResult,
    ) -> DecisionRecord:
        return DecisionRecord(
            decision_id=f"dec_{uuid4().hex}",
            broker_id=context.broker_id,
            load_id=context.load.load_id,
            trigger_event_id=context.event.event_id,
            sop_rule_id=context.active_rule.rule_id,
            disposition=validation.disposition,
            model_disposition=proposal.model_disposition,
            review_status=validation.review_status,
            review_reason=validation.review_reason,
            sop_document_id=context.active_document.document_id,
            sop_version=context.active_document.version,
            sop_content_hash=context.active_document.content_hash,
            ruleset_version=context.active_rule.version,
            sop_section_ref=proposal.sop_section_ref,
            supporting_passage=proposal.supporting_passage,
            load_evidence_refs=tuple(proposal.load_evidence),
            proposed_actions=validation.proposed_actions,
            provider_id=self._settings.llm_provider,
            model_id=self._settings.llm_model_id,
            prompt_schema_version=proposal.schema_version,
            uncertainty_level=proposal.uncertainty,
            validation_status=validation.validation_status,
            validation_reasons=validation.validation_reasons,
            created_at=datetime.now(UTC),
        )

    async def _prepare_request_actions(
        self,
        actions: tuple[ProposedAction, ...],
        context: DecisionContext,
    ) -> tuple[tuple[ProposedAction, ...], str | None]:
        eta_request_action = next(
            (
                action
                for action in actions
                if action.action_type == ActionType.SMS_SEND
                and action.payload.get("request_type") == RequestType.ETA.value
            ),
            None,
        )
        request_id: str | None = None
        created_request_id: str | None = None
        if eta_request_action is not None:
            existing = next(
                (
                    request
                    for request in context.open_requests
                    if request.request_type == RequestType.ETA
                    and request.state == RequestState.OPEN
                ),
                None,
            )
            if existing is not None:
                request_id = existing.request_id
            else:
                request_id = f"req_{uuid4().hex}"
                expires_at = context.event.occurred_at + timedelta(
                    seconds=self._settings.eta_request_timeout_seconds
                )
                request = OpenRequest(
                    request_id=request_id,
                    load_id=context.load.load_id,
                    request_type=RequestType.ETA,
                    question_text=eta_request_action.payload["message"],
                    created_at=datetime.now(UTC),
                    expires_at=expires_at,
                    source_contact_id=eta_request_action.contact_id,
                    last_prompted_at=datetime.now(UTC),
                    correlation_id=context.event.correlation_id,
                )
                await self._request_store.create(context.broker_id, request)
                created_request_id = request_id

        executable: list[ProposedAction] = []
        for action in actions:
            if action.action_type != ActionType.TIMER_SET or request_id is None:
                executable.append(action)
                continue
            timer_context = dict(action.payload.get("context", {}))
            timer_context["request_id"] = request_id
            executable.append(
                action.model_copy(
                    update={
                        "payload": {
                            **action.payload,
                            "context": timer_context,
                        }
                    }
                )
            )
        return tuple(executable), created_request_id

    async def _resolve_usable_eta(
        self,
        context: DecisionContext,
        reason_code: str,
    ) -> None:
        if context.event.event_type != EventType.MESSAGE:
            return
        answer = context.event.payload_json.get("text")
        if not isinstance(answer, str):
            return
        request = next(
            (
                candidate
                for candidate in context.open_requests
                if candidate.request_type == RequestType.ETA
                and candidate.state == RequestState.OPEN
            ),
            None,
        )
        if request is None:
            return
        await self._request_store.resolve_answer(
            context.broker_id,
            context.load.load_id,
            request.request_id,
            answer,
            explicitly_answers_question=reason_code == "explicit_eta_answer",
        )

    async def _open_timer_request(
        self,
        context: DecisionContext,
    ) -> OpenRequest | None:
        if context.event.event_type != EventType.TIMER:
            return None
        request_id = context.event.payload_json.get("request_id")
        if not isinstance(request_id, str):
            return None
        stored_request = await self._request_store.get_open_request(
            context.broker_id,
            context.load.load_id,
            request_id,
        )
        request = _open_request_from_store(stored_request)
        if (
            request is None
            or request.state != RequestState.OPEN
            or (
                request.expires_at is not None
                and context.event.occurred_at < request.expires_at
            )
        ):
            return None
        return request

    async def _timer_request_is_current(
        self,
        context: DecisionContext,
        request: OpenRequest,
    ) -> bool:
        stored_request = await self._request_store.get_open_request(
            context.broker_id,
            context.load.load_id,
            request.request_id,
        )
        current = _open_request_from_store(stored_request)
        return (
            current is not None
            and current.state == RequestState.OPEN
            and current.request_id == request.request_id
            and (
                current.expires_at is None
                or context.event.occurred_at >= current.expires_at
            )
        )

    def _system_no_action(
        self,
        context: DecisionContext,
        *,
        reason: str,
    ) -> DecisionRecord:
        return DecisionRecord(
            decision_id=f"dec_{uuid4().hex}",
            broker_id=context.broker_id,
            load_id=context.load.load_id,
            trigger_event_id=context.event.event_id,
            sop_rule_id=context.active_rule.rule_id,
            disposition=DecisionDisposition.NO_ACTION,
            review_status=ReviewStatus.NOT_REQUIRED,
            sop_document_id=context.active_document.document_id,
            sop_version=context.active_document.version,
            sop_content_hash=context.active_document.content_hash,
            ruleset_version=context.active_rule.version,
            validation_status=ValidationStatus.ACCEPTED,
            validation_reasons=(reason,),
            created_at=datetime.now(UTC),
        )

    def _failed_validation_record(
        self,
        context: DecisionContext,
        *,
        reason: str,
    ) -> DecisionRecord:
        return DecisionRecord(
            decision_id=f"dec_{uuid4().hex}",
            broker_id=context.broker_id,
            load_id=context.load.load_id,
            trigger_event_id=context.event.event_id,
            sop_rule_id=context.active_rule.rule_id,
            disposition=DecisionDisposition.NO_ACTION,
            review_status=ReviewStatus.REQUIRED,
            review_reason=reason,
            sop_document_id=context.active_document.document_id,
            sop_version=context.active_document.version,
            sop_content_hash=context.active_document.content_hash,
            ruleset_version=context.active_rule.version,
            validation_status=ValidationStatus.REJECTED,
            validation_reasons=(reason,),
            created_at=datetime.now(UTC),
        )

    def _provider_failure_record(
        self,
        context: DecisionContext,
        *,
        error_type: str,
    ) -> DecisionRecord:
        return DecisionRecord(
            decision_id=f"dec_{uuid4().hex}",
            broker_id=context.broker_id,
            load_id=context.load.load_id,
            trigger_event_id=context.event.event_id,
            sop_rule_id=context.active_rule.rule_id,
            disposition=None,
            review_status=ReviewStatus.REQUIRED,
            review_reason=error_type,
            sop_document_id=context.active_document.document_id,
            sop_version=context.active_document.version,
            sop_content_hash=context.active_document.content_hash,
            ruleset_version=context.active_rule.version,
            provider_id=self._settings.llm_provider,
            model_id=self._settings.llm_model_id,
            prompt_schema_version="1",
            validation_status=ValidationStatus.PROVIDER_FAILED,
            validation_reasons=(error_type,),
            created_at=datetime.now(UTC),
        )


def _open_request_from_store(value: object) -> OpenRequest | None:
    if value is None:
        return None
    if isinstance(value, OpenRequest):
        return value
    if isinstance(value, Mapping):
        values = dict(value)
    else:
        return None
    values.pop("broker_id", None)
    return OpenRequest.model_validate(values)


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))
