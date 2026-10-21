"""Seed fixed, non-sensitive demonstration records in an explicitly demo DB."""

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path

from app.memory.database import Database
from app.memory.sop_repository import SOPRepository
from config.settings import Settings, get_settings
from models.common import EventType, LoadStage
from models.sop import SOPDocument, SOPRule, SOPScope

logger = logging.getLogger(__name__)

DEMO_BROKER_ID = "demo_broker"
DEMO_LOAD_ID = "demo_load"
DEMO_DOCUMENT_ID = "demo_standard_v1"
DEMO_RULE_ID = "demo_ping_no_action_v1"


async def seed_demo_data(settings: Settings) -> None:
    if settings.environment.strip().lower() != "demo":
        raise RuntimeError(
            "refusing to seed: set FREIGHT_ENVIRONMENT=demo for a dedicated demo database"
        )
    demo_sop_content = (
        Path(__file__).resolve().parents[2] / "examples" / "demo_sop.md"
    ).read_text(encoding="utf-8")
    database = Database(settings)
    await database.open()
    try:
        pool = database.pool
        await pool.execute(
            """
            INSERT INTO loads (
                load_id, broker_id, broker_name, current_stage, delay_risk,
                available_facts_json, status
            )
            VALUES ($1, $2, 'Freight Demo', 'at_pickup', 'low', '{}'::jsonb, 'active')
            ON CONFLICT (broker_id, load_id) DO NOTHING
            """,
            DEMO_LOAD_ID,
            DEMO_BROKER_ID,
        )
        load = await pool.fetchrow(
            """
            SELECT broker_name, current_stage, delay_risk,
                   available_facts_json, status
            FROM loads WHERE broker_id = $1 AND load_id = $2
            """,
            DEMO_BROKER_ID,
            DEMO_LOAD_ID,
        )
        if load is None or any(
            (
                load["broker_name"] != "Freight Demo",
                load["current_stage"] != "at_pickup",
                load["delay_risk"] != "low",
                load["available_facts_json"] not in ({}, "{}"),
                load["status"] != "active",
            )
        ):
            raise ValueError("demo load ID exists with conflicting data")

        document = SOPDocument(
            document_id=DEMO_DOCUMENT_ID,
            chapter_name="Demo Standard Freight Operations",
            version="v1",
            content_markdown=demo_sop_content,
            content_hash=f"sha256:{hashlib.sha256(demo_sop_content.encode()).hexdigest()}",
        )
        rule = SOPRule(
            rule_id=DEMO_RULE_ID,
            document_id=DEMO_DOCUMENT_ID,
            scope=SOPScope.STANDARD,
            load_stage=LoadStage.AT_PICKUP,
            section_ref="demo.ping",
            applicable_event_types=[EventType.PING],
            version="v1",
            effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        )
        existing_rule = await pool.fetchrow(
            """
            SELECT document_id, client_id, load_type, scope, load_stage,
                   section_ref, applicable_event_types, priority, version,
                   effective_from, effective_to
            FROM sop_rules WHERE rule_id = $1
            """,
            rule.rule_id,
        )
        if existing_rule is not None:
            applicable_event_types = existing_rule["applicable_event_types"]
            if isinstance(applicable_event_types, str):
                applicable_event_types = json.loads(applicable_event_types)
            expected_fields = {
                "document_id": rule.document_id,
                "client_id": rule.client_id,
                "load_type": rule.load_type,
                "scope": rule.scope.value,
                "load_stage": rule.load_stage.value,
                "section_ref": rule.section_ref,
                "applicable_event_types": [event.value for event in rule.applicable_event_types],
                "priority": rule.priority,
                "version": rule.version,
                "effective_from": rule.effective_from,
                "effective_to": rule.effective_to,
            }
            if any(existing_rule[key] != value for key, value in expected_fields.items()):
                raise ValueError("demo SOP rule ID exists with conflicting data")
            await SOPRepository(pool).store_document_and_rules(document, [])
        else:
            await SOPRepository(pool).store_document_and_rules(document, [rule])
    finally:
        await database.close()
    logger.info(
        "demo_data_seeded",
        extra={"broker_id": DEMO_BROKER_ID, "load_id": DEMO_LOAD_ID},
    )


async def main() -> None:
    await seed_demo_data(get_settings())


if __name__ == "__main__":
    asyncio.run(main())
