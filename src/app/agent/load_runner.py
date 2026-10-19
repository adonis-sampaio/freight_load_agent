"""Apply structured lifecycle and risk updates before decision-context building."""

import logging
from datetime import datetime
from typing import Protocol

from app.agent.stage_tracker import StageTracker
from models.common import DelayRisk, EventType, LoadStage
from models.event import LoadEvent
from models.load import Load, Stop

logger = logging.getLogger(__name__)

_FACT_ALLOWLIST = frozenset(
    {
        "po_number",
        "pickup_appointment",
        "delivery_appointment",
        "trailer_temperature",
        "seal_number",
        "estimated_arrival",
    }
)


class LoadStateRepository(Protocol):
    async def update_operational_state(self, load: Load) -> None: ...


class LoadRunner:
    def __init__(
        self,
        repository: LoadStateRepository,
        *,
        stage_tracker: StageTracker | None = None,
    ) -> None:
        self._repository = repository
        self._stage_tracker = stage_tracker or StageTracker()

    async def apply_event(
        self,
        broker_id: str,
        event: LoadEvent,
        load: Load,
    ) -> Load:
        if broker_id != load.broker_id or event.load_id != load.load_id:
            raise ValueError("event and broker must match the supplied load")

        updated = load
        if event.event_type == EventType.STAGE_CHANGE:
            stage = event.payload_json.get("current_stage")
            if isinstance(stage, str):
                updated = self._stage_tracker.transition(load, stage)
            else:
                raise ValueError("stage_change events require current_stage")
        elif event.event_type == EventType.METADATA_UPDATE:
            updated = self._apply_metadata_update(load, event)
        elif event.event_type == EventType.PING:
            updated = self._apply_ping(load, event)

        if updated != load:
            await self._repository.update_operational_state(updated)
        return updated

    @staticmethod
    def _apply_metadata_update(load: Load, event: LoadEvent) -> Load:
        field = event.payload_json.get("field")
        value = event.payload_json.get("value")
        if not isinstance(field, str) or field not in _FACT_ALLOWLIST:
            return load
        return load.model_copy(
            update={"available_facts": {**load.available_facts, field: value}}
        )

    @staticmethod
    def _apply_ping(load: Load, event: LoadEvent) -> Load:
        explicit_risk = event.payload_json.get("delay_risk")
        if explicit_risk is not None:
            risk = DelayRisk(explicit_risk)
        else:
            estimated_arrival = _parse_aware_datetime(
                event.payload_json.get("estimated_arrival")
            )
            stop = _target_stop(load, event.payload_json.get("stop_id"))
            if estimated_arrival is None or stop is None:
                logger.warning(
                    "ping_missing_risk_evidence",
                    extra={
                        "broker_id": load.broker_id,
                        "load_id": load.load_id,
                        "event_id": event.event_id,
                    },
                )
                return load
            risk = _risk_for_appointment(estimated_arrival, stop)
        return load.model_copy(update={"delay_risk": risk})


def _parse_aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result


def _target_stop(load: Load, stop_id: object) -> Stop | None:
    if isinstance(stop_id, str):
        return next((stop for stop in load.stops if stop.stop_id == stop_id), None)

    expected_type = (
        "pickup"
        if load.current_stage in {LoadStage.DISPATCHED, LoadStage.TO_PICKUP}
        else "delivery"
    )
    return next(
        (stop for stop in sorted(load.stops, key=lambda item: item.sequence)
         if stop.type.value == expected_type),
        None,
    )


def _risk_for_appointment(estimated_arrival: datetime, stop: Stop) -> DelayRisk:
    if stop.appointment_type is not None and stop.appointment_type.value == "fixed":
        if stop.appointment_time is None:
            raise ValueError("fixed appointment stop is missing appointment_time")
        return (
            DelayRisk.HIGH
            if estimated_arrival > stop.appointment_time
            else DelayRisk.LOW
        )

    if stop.appointment_start is None or stop.appointment_end is None:
        raise ValueError("appointment-window stop is missing its boundaries")
    if estimated_arrival <= stop.appointment_start:
        return DelayRisk.LOW
    if estimated_arrival <= stop.appointment_end:
        return DelayRisk.MEDIUM
    return DelayRisk.HIGH
