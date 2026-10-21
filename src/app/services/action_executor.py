"""Execute only accepted proposals and persist action acknowledgements."""

from datetime import UTC, datetime
from typing import Mapping
from uuid import uuid4

from adapters.action_adapter import (
    AdapterAcknowledgement,
    AdapterError,
    AdapterUnavailableError,
    GenericActionAdapter,
)
from adapters.email_adapter import EmailAdapter
from adapters.sms_adapter import SMSAdapter
from adapters.task_adapter import TaskAdapter
from adapters.timer_adapter import TimerAdapter, TimerRequest
from app.agent.decision_context import DecisionContext
from app.memory.action_ledger import ActionLedger
from app.services.contact_validation import (
    InvalidContactError,
    resolve_action_contact,
)
from models.action import ActionActor, ActionStatus, ActionType, LoadAction
from models.contact import ContactChannel
from models.decision import (
    DecisionDisposition,
    DecisionRecord,
    ValidationStatus,
    ProposedAction,
)
from models.event import LoadEvent


class ActionNotAuthorizedError(RuntimeError):
    """Raised when a decision record does not authorize side effects."""


class ActionExecutionError(RuntimeError):
    """Raised after a failed external action has been recorded."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ActionExecutor:
    def __init__(
        self,
        ledger: ActionLedger,
        *,
        sms: SMSAdapter,
        email: EmailAdapter,
        task: TaskAdapter,
        timer: TimerAdapter,
        additional_adapters: Mapping[ActionType, GenericActionAdapter] | None = None,
    ) -> None:
        self._ledger = ledger
        self._sms = sms
        self._email = email
        self._task = task
        self._timer = timer
        self._additional_adapters = dict(additional_adapters or {})

    async def execute(
        self,
        decision: DecisionRecord,
        context: DecisionContext,
        *,
        actions: tuple[ProposedAction, ...] | None = None,
    ) -> tuple[LoadAction, ...]:
        if decision.disposition == DecisionDisposition.NO_ACTION:
            return await self.record_no_action(
                context.broker_id,
                context.event,
                sop_rule_id=decision.sop_rule_id,
            )

        if (
            decision.validation_status != ValidationStatus.ACCEPTED
            or decision.disposition != DecisionDisposition.PROPOSE_ACTION
        ):
            raise ActionNotAuthorizedError(
                "decision validation did not authorize external actions"
            )
        executable_actions = (
            decision.proposed_actions if actions is None else actions
        )
        if not executable_actions:
            raise ActionNotAuthorizedError(
                "accepted action decision contains no actions"
            )

        results = []
        for proposed_action in executable_actions:
            results.append(
                await self._execute_action(decision, context, proposed_action)
            )
        return tuple(results)

    async def record_no_action(
        self,
        broker_id: str,
        event: LoadEvent,
        *,
        sop_rule_id: str | None = None,
    ) -> tuple[LoadAction, ...]:
        action = LoadAction(
            action_id=f"act_{uuid4().hex}",
            load_id=event.load_id,
            action_type=ActionType.STATUS_UPDATE,
            status=ActionStatus.NO_ACTION,
            trigger_event_id=event.event_id,
            sop_rule_id=sop_rule_id,
            actor=ActionActor.AGENT,
            correlation_id=event.correlation_id or f"corr_{uuid4().hex}",
        )
        await self._ledger.record(broker_id, action)
        return (action,)

    async def _execute_action(
        self,
        decision: DecisionRecord,
        context: DecisionContext,
        proposed_action: ProposedAction,
    ) -> LoadAction:
        action = LoadAction(
            action_id=f"act_{uuid4().hex}",
            load_id=context.load.load_id,
            action_type=proposed_action.action_type,
            status=ActionStatus.QUEUED,
            trigger_event_id=context.event.event_id,
            sop_rule_id=decision.sop_rule_id,
            actor=ActionActor.AGENT,
            payload_json=proposed_action.payload,
            correlation_id=context.event.correlation_id or f"corr_{uuid4().hex}",
            created_at=datetime.now(UTC),
        )
        await self._ledger.record(context.broker_id, action)
        try:
            acknowledgement = await self._dispatch(proposed_action, context)
            if not acknowledgement.accepted:
                raise ActionExecutionError(
                    "adapter_rejected",
                    f"{proposed_action.action_type} adapter rejected the action",
                )
            status = _acknowledgement_status(acknowledgement.status)
        except InvalidContactError as error:
            await self._ledger.update_status(
                context.broker_id,
                context.load.load_id,
                action.action_id,
                ActionStatus.FAILED,
                failure_reason=f"invalid_contact:{error.reason_code}",
            )
            raise ActionExecutionError(
                f"invalid_contact:{error.reason_code}",
                f"contact validation failed: {error.reason_code}",
            ) from error
        except (AdapterError, ActionExecutionError, ValueError) as error:
            if isinstance(error, ActionExecutionError):
                reason_code = error.reason_code
            elif isinstance(error, AdapterUnavailableError):
                reason_code = "adapter_unavailable"
            elif isinstance(error, AdapterError):
                reason_code = "adapter_failed"
            else:
                reason_code = "invalid_action_payload"
            await self._ledger.update_status(
                context.broker_id,
                context.load.load_id,
                action.action_id,
                ActionStatus.FAILED,
                failure_reason=reason_code,
            )
            if isinstance(error, ActionExecutionError):
                raise
            raise ActionExecutionError(
                reason_code,
                f"{proposed_action.action_type} adapter execution failed",
            ) from error

        await self._ledger.update_status(
            context.broker_id,
            context.load.load_id,
            action.action_id,
            status,
            acknowledgement.external_id,
        )
        return action.model_copy(
            update={
                "status": status,
                "external_id": acknowledgement.external_id,
                "completed_at": datetime.now(UTC)
                if status
                in {
                    ActionStatus.DELIVERED,
                    ActionStatus.COMPLETED,
                    ActionStatus.FAILED,
                    ActionStatus.SIMULATED,
                }
                else None,
            }
        )

    async def _dispatch(
        self,
        action: ProposedAction,
        context: DecisionContext,
    ) -> AdapterAcknowledgement:
        payload = action.payload
        if action.action_type == ActionType.SMS_SEND:
            contact = resolve_action_contact(
                action,
                context.contacts,
                load_id=context.load.load_id,
                expected_channel=ContactChannel.SMS,
            )
            return await self._sms.send(contact.external_ref, payload["message"])
        if action.action_type == ActionType.EMAIL_SEND:
            contact = resolve_action_contact(
                action,
                context.contacts,
                load_id=context.load.load_id,
                expected_channel=ContactChannel.EMAIL,
            )
            return await self._email.send(
                payload["thread_id"],
                payload["to"],
                payload["subject"],
                payload["body"],
            )
        if action.action_type == ActionType.HERO_TASK:
            return await self._task.create(
                context.load.load_id,
                payload.get("priority", "normal"),
                payload["task_type"],
                payload["summary"],
            )
        if action.action_type == ActionType.TIMER_SET:
            trigger_value = payload["trigger_at"]
            if not isinstance(trigger_value, str):
                raise ValueError("timer trigger_at must be an ISO datetime string")
            trigger_at = datetime.fromisoformat(
                trigger_value.replace("Z", "+00:00")
            )
            timer_context = payload.get("context", {})
            if not isinstance(timer_context, dict):
                raise ValueError("timer context must be an object")
            return await self._timer.schedule(
                TimerRequest(
                    load_id=context.load.load_id,
                    timer_name=payload["timer_name"],
                    trigger_at=trigger_at,
                    context=timer_context,
                )
            )
        adapter = self._additional_adapters.get(action.action_type)
        if adapter is None:
            raise AdapterUnavailableError(
                f"no adapter configured for {action.action_type}"
            )
        return await adapter.execute(context.load.load_id, payload)


def _acknowledgement_status(status: str) -> ActionStatus:
    statuses = {
        "sent": ActionStatus.SENT,
        "delivered": ActionStatus.DELIVERED,
        "queued": ActionStatus.QUEUED,
        "scheduled": ActionStatus.QUEUED,
        "created": ActionStatus.COMPLETED,
        "completed": ActionStatus.COMPLETED,
        "acknowledged": ActionStatus.COMPLETED,
        "simulated": ActionStatus.SIMULATED,
    }
    try:
        return statuses[status]
    except KeyError as error:
        raise ActionExecutionError(
            "unsupported_adapter_status",
            f"adapter returned unsupported status {status!r}",
        ) from error
