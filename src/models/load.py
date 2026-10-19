"""Load and stop records."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from .common import DelayRisk, LoadStage, LoadStatus, StrictModel


class StopType(StrEnum):
    """Pickup or delivery stop kind."""

    PICKUP = "pickup"
    DELIVERY = "delivery"


class AppointmentType(StrEnum):
    """Appointment representation at a stop."""

    WINDOW = "window"
    FIXED = "fixed"


class Stop(StrictModel):
    stop_id: str = Field(min_length=1)
    sequence: int = Field(gt=0)
    type: StopType
    name: str | None = None
    address: str | None = None
    timezone: str | None = None
    appointment_type: AppointmentType | None = None
    appointment_start: AwareDatetime | None = None
    appointment_end: AwareDatetime | None = None
    appointment_time: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_appointment_window(self) -> "Stop":
        if (
            self.appointment_start is not None
            and self.appointment_end is not None
            and self.appointment_start > self.appointment_end
        ):
            raise ValueError("appointment_start must not be after appointment_end")
        return self


class Load(StrictModel):
    load_id: str = Field(min_length=1)
    broker_id: str = Field(min_length=1)
    broker_name: str = Field(min_length=1)
    shipper_name: str | None = None
    carrier_name: str | None = None
    carrier_mc_number: str | None = None
    commodity: str | None = None
    load_type: str | None = None
    current_stage: LoadStage
    delay_risk: DelayRisk = DelayRisk.LOW
    status: LoadStatus = LoadStatus.ACTIVE
    available_facts: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    stops: list[Stop] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stop_order(self) -> "Load":
        ordered_stops = sorted(self.stops, key=lambda stop: stop.sequence)
        sequences = [stop.sequence for stop in ordered_stops]
        if len(sequences) != len(set(sequences)):
            raise ValueError("stop sequence must be unique within a load")

        pickup_seen = False
        for stop in ordered_stops:
            if stop.type == StopType.PICKUP:
                pickup_seen = True
            elif stop.type == StopType.DELIVERY and not pickup_seen:
                raise ValueError("a delivery stop must follow a pickup stop")
        return self
