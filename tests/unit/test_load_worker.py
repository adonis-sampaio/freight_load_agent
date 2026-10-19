from datetime import UTC, datetime
from typing import Any

import pytest

from app.agent.decision_context import DecisionContext
from app.agent.decision_context import NoApplicableSOPError
from models.common import EventChannel, EventType
from models.contact import Contact
from models.event import LoadEvent
from models.load import Load
from tests.integration.agent_support import make_context
from workers.load_worker import LoadWorker


class FakeTimeline:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.finished: list[tuple[str, str, str, bool]] = []

    async def claim_next_event(self) -> dict[str, Any] | None:
        row, self.row = self.row, None
        return row

    async def finish_event(
        self,
        broker_id: str,
        load_id: str,
        event_id: str,
        *,
        failed: bool = False,
    ) -> None:
        self.finished.append((broker_id, load_id, event_id, failed))


class FakeLoads:
    def __init__(self) -> None:
        self.load = Load(
            load_id="load-1",
            broker_id="broker-1",
            broker_name="Broker",
            current_stage="at_pickup",
        )
        self.contacts: list[Contact] = []

    async def get_load(self, _broker_id: str, _load_id: str) -> Load:
        return self.load

    async def get_contacts(
        self, _broker_id: str, _load_id: str
    ) -> list[Contact]:
        return self.contacts

    async def update_operational_state(self, load: Load) -> None:
        self.load = load


class FakeContextBuilder:
    def __init__(self, *, no_sop: bool = False) -> None:
        self.no_sop = no_sop
        self.args: tuple[Any, ...] | None = None

    async def build(
        self,
        broker_id: str,
        event: LoadEvent,
        load: Load,
        contacts: list[Contact],
    ) -> DecisionContext:
        self.args = (broker_id, event, load, contacts)
        if self.no_sop:
            raise NoApplicableSOPError("no active SOP matches the event")
        return make_context(
            event.event_id,
            event.event_type,
            payload=event.payload_json,
            occurred_at=event.occurred_at,
        )


class FakeDecisionEngine:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.context: DecisionContext | None = None
        self.unmatched: list[tuple[str, LoadEvent, Load]] = []

    async def process(self, context: DecisionContext) -> object:
        self.context = context
        if self.fail:
            raise RuntimeError("engine failed")
        return object()

    async def record_no_applicable_sop(
        self, broker_id: str, event: LoadEvent, load: Load
    ) -> object:
        self.unmatched.append((broker_id, event, load))
        return object()


def event_row() -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "event_id": "event-1",
        "broker_id": "broker-1",
        "load_id": "load-1",
        "event_type": EventType.PING,
        "source_channel": EventChannel.SYSTEM,
        "source_contact_id": None,
        "payload_json": {"latitude": 1.0},
        "processing_status": "processing",
        "occurred_at": now,
        "ingested_at": now,
        "correlation_id": "corr-1",
    }


@pytest.mark.asyncio
async def test_worker_processes_and_completes_claimed_event() -> None:
    timeline = FakeTimeline(event_row())
    loads = FakeLoads()
    builder = FakeContextBuilder()
    engine = FakeDecisionEngine()
    worker = LoadWorker(timeline, loads, builder, engine)

    assert await worker.run_once() is True
    assert builder.args is not None
    assert builder.args[0] == "broker-1"
    assert builder.args[1].event_id == "event-1"
    assert timeline.finished == [("broker-1", "load-1", "event-1", False)]
    assert engine.context is not None


@pytest.mark.asyncio
async def test_worker_marks_failed_event_and_surfaces_processing_error() -> None:
    timeline = FakeTimeline(event_row())
    worker = LoadWorker(
        timeline,
        FakeLoads(),
        FakeContextBuilder(),
        FakeDecisionEngine(fail=True),
    )

    with pytest.raises(RuntimeError, match="engine failed"):
        await worker.run_once()

    assert timeline.finished == [("broker-1", "load-1", "event-1", True)]


@pytest.mark.asyncio
async def test_worker_returns_false_when_no_event_is_queued() -> None:
    timeline = FakeTimeline(None)
    worker = LoadWorker(
        timeline,
        FakeLoads(),
        FakeContextBuilder(),
        FakeDecisionEngine(),
    )

    assert await worker.run_once() is False
    assert timeline.finished == []


@pytest.mark.asyncio
async def test_worker_records_no_action_when_no_sop_applies() -> None:
    timeline = FakeTimeline(event_row())
    builder = FakeContextBuilder(no_sop=True)
    engine = FakeDecisionEngine()
    worker = LoadWorker(timeline, FakeLoads(), builder, engine)

    assert await worker.run_once() is True
    assert len(engine.unmatched) == 1
    assert engine.unmatched[0][1].event_id == "event-1"
    assert timeline.finished == [("broker-1", "load-1", "event-1", False)]
