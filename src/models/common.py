"""Shared domain enumerations and model configuration."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for validated records that reject unexpected fields."""

    model_config = ConfigDict(extra="forbid")


class LoadStage(StrEnum):
    DISPATCHED = "dispatched"
    TO_PICKUP = "to_pickup"
    AT_PICKUP = "at_pickup"
    TO_DELIVERY = "to_delivery"
    AT_DELIVERY = "at_delivery"
    DELIVERED = "delivered"


class DelayRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LoadStatus(StrEnum):
    ACTIVE = "active"
    HELD = "held"
    RESOLVED = "resolved"
    EXCEPTION = "exception"


class EventType(StrEnum):
    MESSAGE = "message"
    PING = "ping"
    TIMER = "timer"
    ATTACHMENT = "attachment"
    METADATA_UPDATE = "metadata_update"
    STAGE_CHANGE = "stage_change"


class EventChannel(StrEnum):
    SMS = "sms"
    EMAIL = "email"
    SLACK = "slack"
    INTERNAL_TIMER = "internal_timer"
    SYSTEM = "system"
