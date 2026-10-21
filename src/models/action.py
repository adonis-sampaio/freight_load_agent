"""Action catalog and persisted action records."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from .common import StrictModel


class ActionType(StrEnum):
    SMS_SEND = "sms_send"
    EMAIL_SEND = "email_send"
    SLACK_ALERT = "slack_alert"
    TMS_UPDATE = "tms_update"
    HERO_TASK = "hero_task"
    TIMER_SET = "timer_set"
    STATUS_UPDATE = "status_update"


class ActionStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    COMPLETED = "completed"
    NO_ACTION = "no_action"
    SIMULATED = "simulated"


class ActionActor(StrEnum):
    AGENT = "agent"
    HERO = "hero"
    BROKER = "broker"
    INTERNAL_SYSTEM = "internal_system"


class LoadAction(StrictModel):
    action_id: str = Field(min_length=1)
    load_id: str = Field(min_length=1)
    action_type: ActionType
    status: ActionStatus
    trigger_event_id: str = Field(min_length=1)
    sop_rule_id: str | None = None
    actor: ActionActor
    payload_json: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    external_id: str | None = None
    failure_reason: str | None = Field(default=None, max_length=120)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_no_action_payload(self) -> "LoadAction":
        if self.status == ActionStatus.NO_ACTION and self.payload_json:
            raise ValueError("no-action records cannot contain an outbound payload")
        return self
