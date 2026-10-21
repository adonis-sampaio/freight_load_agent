import os
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.memory.load_timeline import LoadTimelineRepository
from app.memory.sop_repository import SOPRepository
from config.settings import Settings
from models.common import EventChannel, EventType, LoadStage
from models.event import LoadEvent
from models.sop import SOPDocument, SOPRule, SOPScope
from workers.worker_composition import build_load_worker

TEST_DATABASE_URL = os.getenv("FREIGHT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set FREIGHT_TEST_DATABASE_URL to a disposable PostgreSQL database",
)


@pytest.mark.asyncio
async def test_worker_persists_demo_decision_for_queued_postgres_event() -> None:
    assert TEST_DATABASE_URL is not None
    schema = f"worker_test_{uuid4().hex}"
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    pool: asyncpg.Pool | None = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            TEST_DATABASE_URL,
            server_settings={"search_path": schema},
        )
        root = Path(__file__).resolve().parents[2]
        for migration_name in (
            "001_initial_schema.sql",
            "002_worker_claim_recovery.sql",
        ):
            migration = (root / "migrations" / migration_name).read_text()
            for statement in migration.split(";"):
                if statement.strip():
                    await pool.execute(statement)

        broker_id = f"demo_{uuid4().hex}"
        load_id = f"load_{uuid4().hex}"
        event_id = f"event_{uuid4().hex}"
        document_id = f"document_{uuid4().hex}"
        rule_id = f"rule_{uuid4().hex}"
        now = datetime.now(UTC)
        await pool.execute(
            """
            INSERT INTO loads (load_id, broker_id, broker_name, current_stage)
            VALUES ($1, $2, 'Integration Demo', 'at_pickup')
            """,
            load_id,
            broker_id,
        )
        document = SOPDocument(
            document_id=document_id,
            chapter_name="Integration Demo SOP",
            version="v1",
            content_markdown=(
                "# Demo\n\n## demo.ping\n\n"
                "This event is a no-action demonstration."
            ),
            content_hash=f"sha256:{uuid4().hex}",
        )
        rule = SOPRule(
            rule_id=rule_id,
            document_id=document_id,
            scope=SOPScope.STANDARD,
            load_stage=LoadStage.AT_PICKUP,
            section_ref="demo.ping",
            applicable_event_types=[EventType.PING],
            version="v1",
            effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        )
        await SOPRepository(pool).store_document_and_rules(document, [rule])
        event = LoadEvent(
            event_id=event_id,
            load_id=load_id,
            event_type=EventType.PING,
            source_channel=EventChannel.SYSTEM,
            payload_json={},
            occurred_at=now,
            ingested_at=now,
        )
        timeline = LoadTimelineRepository(pool)
        await timeline.append_event(broker_id, event)
        worker, _ = build_load_worker(
            pool,
            Settings(database_url=TEST_DATABASE_URL),
        )

        assert await worker.run_once() is True

        stored_event = await pool.fetchrow(
            "SELECT processing_status, processing_started_at FROM load_events WHERE event_id = $1",
            event_id,
        )
        decision = await pool.fetchrow(
            """
            SELECT provider_id, model_id, disposition, validation_status
            FROM decision_records WHERE trigger_event_id = $1
            """,
            event_id,
        )
        assert stored_event["processing_status"] == "completed"
        assert stored_event["processing_started_at"] is None
        assert decision["provider_id"] == "demo"
        assert decision["model_id"] == "deterministic-no-action"
        assert decision["disposition"] == "no_action"
        assert decision["validation_status"] == "accepted"

        queued_events = [
            LoadEvent(
                event_id=f"claim_{uuid4().hex}",
                load_id=load_id,
                event_type=EventType.PING,
                source_channel=EventChannel.SYSTEM,
                payload_json={},
                occurred_at=now,
                ingested_at=now,
            )
            for _ in range(2)
        ]
        for queued_event in queued_events:
            await timeline.append_event(broker_id, queued_event)
        claims = await asyncio.gather(
            timeline.claim_next_event(),
            timeline.claim_next_event(),
        )
        claimed_ids = [claim["event_id"] for claim in claims if claim is not None]
        assert len(claimed_ids) == 2
        assert len(set(claimed_ids)) == 2

        await pool.execute(
            """
            UPDATE load_events
            SET processing_started_at = now() - interval '1 hour'
            WHERE event_id = $1
            """,
            claimed_ids[0],
        )
        assert await timeline.requeue_stale_events(900) == 1
        claim_states = await pool.fetch(
            """
            SELECT processing_status, processing_started_at
            FROM load_events WHERE event_id = ANY($1::text[])
            """,
            claimed_ids,
        )
        assert sorted(row["processing_status"] for row in claim_states) == [
            "processing",
            "queued",
        ]
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()
