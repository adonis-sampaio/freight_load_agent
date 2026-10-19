from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.memory.action_ledger import ActionLedger
from app.memory.database import Database
from app.memory.decision_store import (
    DecisionStore,
    LLMUsageBudgetExceededError,
)
from app.memory.load_timeline import LoadTimelineRepository
from app.memory.request_store import RequestStore, RequestTransitionError
from app.memory.sop_repository import SOPRepository
from config.settings import Settings
from models.action import ActionStatus
from models.common import EventChannel, EventType, LoadStage
from models.event import LoadEvent, RequestState


class FakePool:
    def __init__(
        self,
        result: str = "UPDATE 1",
        fetchrow_result: Any = None,
    ) -> None:
        self.result = result
        self.fetchrow_result = fetchrow_result
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        return self.result

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.calls.append((query, args))
        return []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self.fetchrow_result


@pytest.mark.asyncio
async def test_database_requires_configured_database_url() -> None:
    database = Database(Settings(database_url=None))

    with pytest.raises(RuntimeError, match="FREIGHT_DATABASE_URL"):
        await database.open()


@pytest.mark.asyncio
async def test_timeline_rejects_unbounded_page_size() -> None:
    repository = LoadTimelineRepository(FakePool())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="between 1 and 200"):
        await repository.get_recent_events("broker-1", "load-1", limit=201)


@pytest.mark.asyncio
async def test_request_store_does_not_reopen_terminal_request() -> None:
    pool = FakePool()
    repository = RequestStore(pool)  # type: ignore[arg-type]

    with pytest.raises(RequestTransitionError, match="cannot be reopened"):
        await repository.transition(
            "broker-1", "load-1", "request-1", RequestState.OPEN
        )

    assert pool.calls == []


@pytest.mark.asyncio
async def test_action_status_completion_uses_bound_values() -> None:
    pool = FakePool()
    ledger = ActionLedger(pool)  # type: ignore[arg-type]

    await ledger.update_status(
        "broker-1", "load-1", "action-1", ActionStatus.DELIVERED
    )

    query, args = pool.calls[0]
    assert "CASE WHEN $5 THEN now()" in query
    assert args == (
        "broker-1",
        "load-1",
        "action-1",
        ActionStatus.DELIVERED,
        True,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_action_failure_reason_is_persisted_with_status() -> None:
    pool = FakePool()
    ledger = ActionLedger(pool)  # type: ignore[arg-type]

    await ledger.update_status(
        "broker-1",
        "load-1",
        "action-1",
        ActionStatus.FAILED,
        failure_reason="adapter_unavailable",
    )

    query, args = pool.calls[0]
    assert "failure_reason = COALESCE($7, failure_reason)" in query
    assert args == (
        "broker-1",
        "load-1",
        "action-1",
        ActionStatus.FAILED,
        True,
        None,
        "adapter_unavailable",
    )


@pytest.mark.asyncio
async def test_sop_mvp_query_is_standard_only() -> None:
    pool = FakePool()
    repository = SOPRepository(pool)  # type: ignore[arg-type]
    now = datetime.now(UTC)

    await repository.find_standard_sop(
        stage=LoadStage.AT_PICKUP,
        event_type=EventType.PING,
        at=now,
    )

    query, args = pool.calls[0]
    assert "r.scope = 'standard'" in query
    assert "r.client_id IS NULL" in query
    assert args == (LoadStage.AT_PICKUP, EventType.PING, now)


@pytest.mark.asyncio
async def test_sop_query_loads_matching_overrides_and_active_versions() -> None:
    pool = FakePool()
    repository = SOPRepository(pool)  # type: ignore[arg-type]
    now = datetime.now(UTC)

    await repository.find_applicable_sops(
        client_id="broker-1",
        load_type="refrigerated",
        stage=LoadStage.AT_PICKUP,
        event_type=EventType.PING,
        at=now,
    )

    query, args = pool.calls[0]
    assert "r.scope = 'standard'" in query
    assert "r.scope = 'client_override'" in query
    assert "r.scope = 'load_type_override'" in query
    assert "r.effective_from <= $5" in query
    assert "r.effective_to > $5" in query
    assert args == (
        "broker-1",
        "refrigerated",
        LoadStage.AT_PICKUP,
        EventType.PING,
        now,
    )


@pytest.mark.asyncio
async def test_llm_budget_reservation_fails_closed_when_store_rejects_call() -> None:
    pool = FakePool()
    store = DecisionStore(pool)  # type: ignore[arg-type]

    with pytest.raises(LLMUsageBudgetExceededError, match="budget"):
        await store.reserve_llm_call(
            "broker-1",
            "load-1",
            input_tokens=500,
            reserved_output_tokens=1000,
            reserved_cost_usd=0.05,
            maximum_calls=20,
            maximum_cost_usd=1.0,
        )

    query, args = pool.calls[0]
    assert "ON CONFLICT (broker_id, load_id)" in query
    assert "call_count < $6" in query
    assert "reserved_cost_usd + EXCLUDED.reserved_cost_usd <= $7" in query
    assert args[:4] == ("broker-1", "load-1", 500, 1000)


@pytest.mark.asyncio
async def test_llm_budget_reservation_returns_incremented_call_count() -> None:
    pool = FakePool(fetchrow_result={"call_count": 3})
    store = DecisionStore(pool)  # type: ignore[arg-type]

    call_count = await store.reserve_llm_call(
        "broker-1",
        "load-1",
        input_tokens=500,
        reserved_output_tokens=1000,
        reserved_cost_usd=0.05,
        maximum_calls=20,
        maximum_cost_usd=1.0,
    )

    assert call_count == 3
    assert pool.calls[0][1][4:] == (Decimal("0.05"), 20, Decimal("1.0"))


@pytest.mark.asyncio
async def test_llm_latency_updates_require_a_matching_reservation() -> None:
    pool = FakePool(result="UPDATE 0")
    store = DecisionStore(pool)  # type: ignore[arg-type]

    with pytest.raises(LookupError, match="reservation was not found"):
        await store.record_llm_latency("broker-1", "load-1", 123)

    query, args = pool.calls[0]
    assert "total_latency_ms = total_latency_ms + $3" in query
    assert args == ("broker-1", "load-1", 123)


@pytest.mark.asyncio
async def test_timeline_persists_event_correlation_and_payload() -> None:
    pool = FakePool()
    repository = LoadTimelineRepository(pool)  # type: ignore[arg-type]
    event = LoadEvent(
        event_id="event-1",
        load_id="load-1",
        event_type=EventType.MESSAGE,
        source_channel=EventChannel.SMS,
        payload_json={"text": "Where is the PO?"},
        occurred_at=datetime.now(UTC),
        correlation_id="trace-1",
    )

    assert await repository.append_event("broker-1", event) == "event-1"
    query, args = pool.calls[0]
    assert "correlation_id" in query
    assert args[1:6] == (
        "broker-1",
        "load-1",
        EventType.MESSAGE,
        EventChannel.SMS,
        None,
    )
    assert args[6] == '{"text": "Where is the PO?"}'
    assert args[-1] == "trace-1"
