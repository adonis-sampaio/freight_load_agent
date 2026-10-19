"""Durable load-event timeline repository."""

import json
from datetime import datetime
from typing import Any

import asyncpg

from models.event import LoadEvent


class LoadTimelineRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append_event(self, broker_id: str, event: LoadEvent) -> str:
        await self._pool.execute(
            """
            INSERT INTO load_events (
                event_id, broker_id, load_id, event_type, source_channel,
                source_contact_id, payload_json, processing_status,
                occurred_at, ingested_at, correlation_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'queued', $8, $9, $10)
            """,
            event.event_id,
            broker_id,
            event.load_id,
            event.event_type,
            event.source_channel,
            event.source_contact_id,
            json.dumps(event.payload_json),
            event.occurred_at,
            event.ingested_at,
            event.correlation_id,
        )
        return event.event_id

    async def get_event(
        self, broker_id: str, load_id: str, event_id: str
    ) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            """
            SELECT event_id, broker_id, load_id, event_type, source_channel,
                   source_contact_id, payload_json, processing_status,
                   occurred_at, ingested_at, correlation_id
            FROM load_events
            WHERE broker_id = $1 AND load_id = $2 AND event_id = $3
            """,
            broker_id,
            load_id,
            event_id,
        )

    async def get_event_by_correlation_id(
        self,
        broker_id: str,
        load_id: str,
        correlation_id: str,
    ) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            """
            SELECT event_id, broker_id, load_id, event_type, source_channel,
                   source_contact_id, payload_json, processing_status,
                   occurred_at, ingested_at, correlation_id
            FROM load_events
            WHERE broker_id = $1 AND load_id = $2 AND correlation_id = $3
            ORDER BY occurred_at, ingested_at, event_id
            LIMIT 1
            """,
            broker_id,
            load_id,
            correlation_id,
        )

    async def claim_next_event(self) -> asyncpg.Record | None:
        """Atomically claim one queued event across concurrent workers."""
        return await self._pool.fetchrow(
            """
            WITH next_event AS (
                SELECT event_id
                FROM load_events
                WHERE processing_status = 'queued'
                ORDER BY ingested_at, event_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE load_events AS event
            SET processing_status = 'processing'
            FROM next_event
            WHERE event.event_id = next_event.event_id
            RETURNING event.event_id, event.broker_id, event.load_id,
                      event.event_type, event.source_channel,
                      event.source_contact_id, event.payload_json,
                      event.processing_status, event.occurred_at,
                      event.ingested_at, event.correlation_id
            """
        )

    async def finish_event(
        self,
        broker_id: str,
        load_id: str,
        event_id: str,
        *,
        failed: bool = False,
    ) -> None:
        status = "failed" if failed else "completed"
        result = await self._pool.execute(
            """
            UPDATE load_events
            SET processing_status = $4
            WHERE broker_id = $1 AND load_id = $2 AND event_id = $3
              AND processing_status = 'processing'
            """,
            broker_id,
            load_id,
            event_id,
            status,
        )
        if result != "UPDATE 1":
            raise RuntimeError(
                f"event {event_id} was not in processing state"
            )

    async def get_recent_events(
        self,
        broker_id: str,
        load_id: str,
        *,
        before: datetime | None = None,
        limit: int = 20,
    ) -> list[asyncpg.Record]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        return await self._pool.fetch(
            """
            SELECT event_id, broker_id, load_id, event_type, source_channel,
                   source_contact_id, payload_json, processing_status,
                   occurred_at, ingested_at, correlation_id
            FROM load_events
            WHERE broker_id = $1 AND load_id = $2
              AND ($3::timestamptz IS NULL OR occurred_at < $3)
            ORDER BY occurred_at DESC, ingested_at DESC
            LIMIT $4
            """,
            broker_id,
            load_id,
            before,
            limit,
        )


def decode_json_field(record: asyncpg.Record, field: str) -> dict[str, Any]:
    """Normalize asyncpg JSON values for callers that need a mapping."""
    value = record[field]
    if isinstance(value, str):
        decoded = json.loads(value)
    else:
        decoded = value
    if not isinstance(decoded, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return decoded
