"""Run the continuously polling background worker."""

import asyncio
import logging
import signal
from typing import Protocol

from app.memory.database import Database
from config.logging import configure_logging
from config.settings import Settings, get_settings
from workers.worker_composition import build_load_worker

logger = logging.getLogger(__name__)


class WorkerLoop(Protocol):
    async def run_once(self) -> bool: ...


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_worker_loop(
    worker: WorkerLoop,
    database: Database,
    settings: Settings,
    stop_event: asyncio.Event,
) -> None:
    """Poll events, continuing event failures but bounding database retries."""
    failed_poll_attempts = 0
    while not stop_event.is_set():
        try:
            processed = await worker.run_once()
        except Exception:
            logger.exception("worker_iteration_failed")
            try:
                await database.pool.fetchval("SELECT 1")
            except Exception:
                failed_poll_attempts += 1
                if failed_poll_attempts > settings.worker_db_max_retries:
                    logger.exception(
                        "worker_database_retries_exhausted",
                        extra={"attempts": failed_poll_attempts},
                    )
                    raise
                backoff = min(2 ** (failed_poll_attempts - 1), 30)
                logger.exception(
                    "worker_database_unavailable",
                    extra={
                        "attempt": failed_poll_attempts,
                        "retry_in_seconds": backoff,
                    },
                )
                await _wait_or_stop(stop_event, backoff)
                continue
            failed_poll_attempts = 0
            if not stop_event.is_set():
                await _wait_or_stop(
                    stop_event,
                    settings.worker_poll_interval_seconds,
                )
            continue

        failed_poll_attempts = 0
        if stop_event.is_set():
            break
        if not processed:
            await _wait_or_stop(
                stop_event,
                settings.worker_poll_interval_seconds,
            )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(handled_signal, stop_event.set)

    try:
        await database.open()
        await database.pool.fetchval("SELECT 1")
        worker, timeline = build_load_worker(database.pool, settings)
        requeued = await timeline.requeue_stale_events(
            settings.worker_stale_claim_seconds
        )
        logger.info(
            "worker_ready",
            extra={
                "demo_mode": True,
                "requeued_stale_events": requeued,
                "poll_interval_seconds": settings.worker_poll_interval_seconds,
            },
        )
        await run_worker_loop(worker, database, settings, stop_event)
    finally:
        await database.close()
        logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
