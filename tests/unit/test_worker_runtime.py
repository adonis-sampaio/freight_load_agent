import asyncio

import pytest
from pydantic import ValidationError

from app.memory.database import Database
from config.settings import Settings
from workers.run_worker import run_worker_loop
from workers.seed_demo_data import seed_demo_data


class FakePool:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.health_checks = 0

    async def fetchval(self, _query: str) -> int:
        self.health_checks += 1
        if self.fail:
            raise ConnectionError("database unavailable")
        return 1


class FakeWorker:
    def __init__(
        self,
        stop_event: asyncio.Event,
        *,
        fail_first: bool = False,
        stop_on_call: bool = True,
    ) -> None:
        self.stop_event = stop_event
        self.fail_first = fail_first
        self.stop_on_call = stop_on_call
        self.calls = 0

    async def run_once(self) -> bool:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("event failed")
        if self.stop_on_call:
            self.stop_event.set()
        return False


def make_database(pool: FakePool) -> Database:
    database = Database(Settings(database_url="postgresql://demo"))
    database._pool = pool  # type: ignore[assignment]
    return database


def test_worker_settings_validate_polling_and_retry_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(worker_poll_interval_seconds=11)
    with pytest.raises(ValidationError):
        Settings(worker_stale_claim_seconds=59)
    with pytest.raises(ValidationError):
        Settings(worker_db_max_retries=21)


@pytest.mark.asyncio
async def test_demo_seeder_refuses_non_demo_environment() -> None:
    with pytest.raises(RuntimeError, match="FREIGHT_ENVIRONMENT=demo"):
        await seed_demo_data(Settings(environment="production"))


@pytest.mark.asyncio
async def test_worker_loop_stops_without_claiming_a_second_event() -> None:
    stop_event = asyncio.Event()
    worker = FakeWorker(stop_event)

    await run_worker_loop(
        worker,
        make_database(FakePool()),
        Settings(),
        stop_event,
    )

    assert worker.calls == 1


@pytest.mark.asyncio
async def test_worker_waits_for_the_configured_interval_when_idle(monkeypatch) -> None:
    from workers import run_worker

    stop_event = asyncio.Event()
    worker = FakeWorker(stop_event, stop_on_call=False)
    intervals: list[float] = []

    async def stop_after_idle_wait(_stop_event: asyncio.Event, seconds: float) -> None:
        intervals.append(seconds)
        stop_event.set()

    monkeypatch.setattr(run_worker, "_wait_or_stop", stop_after_idle_wait)
    await run_worker_loop(
        worker,
        make_database(FakePool()),
        Settings(worker_poll_interval_seconds=0.25),
        stop_event,
    )

    assert intervals == [0.25]
    assert worker.calls == 1


@pytest.mark.asyncio
async def test_worker_continues_after_event_failure_when_database_is_healthy() -> None:
    stop_event = asyncio.Event()
    worker = FakeWorker(stop_event, fail_first=True)
    pool = FakePool()

    await run_worker_loop(
        worker,
        make_database(pool),
        Settings(worker_poll_interval_seconds=0.01),
        stop_event,
    )

    assert worker.calls == 2
    assert pool.health_checks == 1


@pytest.mark.asyncio
async def test_worker_exits_after_database_retry_budget_is_exhausted() -> None:
    stop_event = asyncio.Event()
    pool = FakePool(fail=True)
    worker = FakeWorker(stop_event, fail_first=True)

    with pytest.raises(ConnectionError, match="database unavailable"):
        await run_worker_loop(
            worker,
            make_database(pool),
            Settings(worker_db_max_retries=0),
            stop_event,
        )

    assert worker.calls == 1
    assert pool.health_checks == 1


@pytest.mark.asyncio
async def test_database_retry_backoff_is_exponential_and_capped(monkeypatch) -> None:
    from workers import run_worker

    class BrokenWorker:
        async def run_once(self) -> bool:
            raise RuntimeError("poll failed")

    stop_event = asyncio.Event()
    delays: list[float] = []

    async def record_delay(_stop_event: asyncio.Event, seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(run_worker, "_wait_or_stop", record_delay)
    with pytest.raises(ConnectionError, match="database unavailable"):
        await run_worker_loop(
            BrokenWorker(),
            make_database(FakePool(fail=True)),
            Settings(worker_db_max_retries=6),
            stop_event,
        )

    assert delays == [1, 2, 4, 8, 16, 30]
