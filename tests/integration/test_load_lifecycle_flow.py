from datetime import UTC, datetime, timedelta

import pytest

from app.agent.load_runner import LoadRunner
from app.agent.stage_tracker import StageTracker, StageTransitionError
from models.common import EventChannel, EventType, LoadStage, DelayRisk
from models.event import LoadEvent
from models.load import Load, Stop


def multi_stop_load() -> Load:
    return Load(
        load_id="load-multi",
        broker_id="broker-1",
        broker_name="Broker",
        current_stage="dispatched",
        stops=[
            Stop(
                stop_id="pickup-1",
                sequence=1,
                type="pickup",
                appointment_time=datetime(2026, 10, 18, 8, tzinfo=UTC),
            ),
            Stop(
                stop_id="delivery-1",
                sequence=2,
                type="delivery",
                appointment_time=datetime(2026, 10, 19, 8, tzinfo=UTC),
            ),
            Stop(
                stop_id="delivery-2",
                sequence=3,
                type="delivery",
                appointment_time=datetime(2026, 10, 20, 8, tzinfo=UTC),
            ),
        ],
    )


def test_load_stage_transitions_follow_the_defined_lifecycle() -> None:
    tracker = StageTracker()
    load = multi_stop_load()

    load = tracker.transition(load, "to_pickup")
    load = tracker.transition(load, "at_pickup")
    load = tracker.transition(load, "to_delivery")
    load = tracker.transition(load, "at_delivery")
    load = tracker.transition(load, "delivered")

    assert load.current_stage == "delivered"


def test_load_stage_transition_rejects_skipping_a_lifecycle_state() -> None:
    with pytest.raises(StageTransitionError, match="next valid stage"):
        StageTracker().transition(multi_stop_load(), "at_pickup")


def test_remaining_stop_actions_exclude_completed_stops() -> None:
    remaining = StageTracker.remaining_stops(
        multi_stop_load(),
        completed_stop_ids={"pickup-1", "delivery-1"},
    )

    assert [stop.stop_id for stop in remaining] == ["delivery-2"]


@pytest.mark.asyncio
async def test_runner_updates_stage_and_ping_risk_from_appointment_window() -> None:
    class StateStore:
        def __init__(self) -> None:
            self.updated: list[Load] = []

        async def update_operational_state(self, load: Load) -> None:
            self.updated.append(load)

    now = datetime.now(UTC)
    load = Load(
        load_id="load-ping",
        broker_id="broker-1",
        broker_name="Broker",
        current_stage=LoadStage.TO_PICKUP,
        stops=[
            Stop(
                stop_id="pickup-1",
                sequence=1,
                type="pickup",
                appointment_type="window",
                appointment_start=now + timedelta(minutes=15),
                appointment_end=now + timedelta(minutes=45),
            )
        ],
    )
    store = StateStore()
    runner = LoadRunner(store)
    stage_event = LoadEvent(
        event_id="stage-event",
        load_id=load.load_id,
        event_type=EventType.STAGE_CHANGE,
        source_channel=EventChannel.SYSTEM,
        payload_json={"current_stage": "at_pickup"},
        occurred_at=now,
    )
    updated = await runner.apply_event("broker-1", stage_event, load)
    ping_event = LoadEvent(
        event_id="ping-event",
        load_id=load.load_id,
        event_type=EventType.PING,
        source_channel=EventChannel.SYSTEM,
        payload_json={
            "estimated_arrival": (now + timedelta(minutes=30)).isoformat(),
            "stop_id": "pickup-1",
        },
        occurred_at=now,
    )

    updated = await runner.apply_event("broker-1", ping_event, updated)

    assert updated.current_stage == LoadStage.AT_PICKUP
    assert updated.delay_risk == DelayRisk.MEDIUM
    assert len(store.updated) == 2


@pytest.mark.asyncio
async def test_ping_without_estimated_arrival_preserves_risk_and_state() -> None:
    class StateStore:
        async def update_operational_state(self, _load: Load) -> None:
            pytest.fail("an unchanged load must not be written")

    load = multi_stop_load()
    event = LoadEvent(
        event_id="ping-without-eta",
        load_id=load.load_id,
        event_type=EventType.PING,
        source_channel=EventChannel.SYSTEM,
        payload_json={"latitude": 47.6, "longitude": -122.3},
        occurred_at=datetime.now(UTC),
    )

    updated = await LoadRunner(StateStore()).apply_event(
        "broker-1",
        event,
        load,
    )

    assert updated == load
