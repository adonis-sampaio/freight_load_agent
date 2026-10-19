"""Inbound event and open-request records."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import EventChannel, EventType, StrictModel


class RequestType(StrEnum):
    ETA = "eta_request"
    TEMPERATURE = "temperature_request"
    SEAL = "seal_request"
    CUSTOM = "custom_question"


class RequestState(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class LoadEvent(StrictModel):
    event_id: str = Field(min_length=1)
    load_id: str = Field(min_length=1)
    event_type: EventType
    source_channel: EventChannel
    source_contact_id: str | None = None
    payload_json: dict[str, Any]
    occurred_at: AwareDatetime
    ingested_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None


class EventIngestRequest(StrictModel):
    broker_id: str = Field(min_length=1)
    load_id: str = Field(min_length=1)
    event_type: EventType
    source_channel: EventChannel
    source_contact_id: str | None = None
    payload: dict[str, Any]
    occurred_at: AwareDatetime
    correlation_id: str | None = None


class EventIngestResponse(StrictModel):
    accepted: Literal[True] = True
    event_id: str
    status: Literal["queued_for_processing"] = "queued_for_processing"


class OpenRequest(StrictModel):
    request_id: str = Field(min_length=1)
    load_id: str = Field(min_length=1)
    request_type: RequestType
    question_text: str = Field(min_length=1)
    state: RequestState = RequestState.OPEN
    reminder_count: int = Field(default=0, ge=0)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: AwareDatetime | None = None
    resolved_at: AwareDatetime | None = None
    source_contact_id: str | None = None
    last_prompted_at: AwareDatetime | None = None
    correlation_id: str | None = None

    @model_validator(mode="after")
    def validate_resolution_timestamp(self) -> "OpenRequest":
        if self.state == RequestState.OPEN and self.resolved_at is not None:
            raise ValueError("an open request cannot have resolved_at set")
        if self.state != RequestState.OPEN and self.resolved_at is None:
            raise ValueError("a closed request must have resolved_at set")
        return self
