from typing import Any

import pytest

from app.memory.load_timeline import LoadTimelineRepository


class FakePool:
    def __init__(
        self,
        result: str = "UPDATE 2",
        fetchrow_result: Any = None,
    ) -> None:
        self.result = result
        self.fetchrow_result = fetchrow_result
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        return self.result

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self.fetchrow_result


@pytest.mark.asyncio
async def test_event_claim_sets_claim_timestamp_atomically() -> None:
    pool = FakePool()
    repository = LoadTimelineRepository(pool)  # type: ignore[arg-type]

    assert await repository.claim_next_event() is None

    query, _ = pool.calls[0]
    assert "FOR UPDATE SKIP LOCKED" in query
    assert "processing_started_at = now()" in query
    assert "event.processing_started_at" in query


@pytest.mark.asyncio
async def test_finishing_event_clears_claim_timestamp() -> None:
    pool = FakePool(result="UPDATE 1")
    repository = LoadTimelineRepository(pool)  # type: ignore[arg-type]

    await repository.finish_event("broker-1", "load-1", "event-1")

    query, _ = pool.calls[0]
    assert "processing_started_at = NULL" in query

@pytest.mark.asyncio
async def test_stale_claim_recovery_only_requeues_expired_processing_events() -> None:
    pool = FakePool()
    repository = LoadTimelineRepository(pool)  # type: ignore[arg-type]

    count = await repository.requeue_stale_events(900)

    query, args = pool.calls[0]
    assert count == 2
    assert "processing_status = 'processing'" in query
    assert "processing_started_at < now()" in query
    assert "processing_started_at = NULL" in query
    assert args == (900.0,)


@pytest.mark.asyncio
async def test_stale_claim_recovery_rejects_nonpositive_threshold() -> None:
    pool = FakePool()
    repository = LoadTimelineRepository(pool)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="must be positive"):
        await repository.requeue_stale_events(0)

    assert pool.calls == []
