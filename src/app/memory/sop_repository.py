"""SOP persistence queries used by decision orchestration."""

from datetime import datetime
import json

import asyncpg

from models.common import EventType, LoadStage
from models.sop import SOPDocument, SOPRule


class SOPRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def store_document_and_rules(
        self,
        document: SOPDocument,
        rules: list[SOPRule] | tuple[SOPRule, ...],
    ) -> None:
        """Persist a document version and its indexes atomically without overwrites."""
        for rule in rules:
            if rule.document_id != document.document_id:
                raise ValueError("SOP rule references a different document")

        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO sop_documents (
                        document_id, client_id, chapter_name, version,
                        content_markdown, content_hash
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (document_id) DO NOTHING
                    """,
                    document.document_id,
                    document.client_id,
                    document.chapter_name,
                    document.version,
                    document.content_markdown,
                    document.content_hash,
                )
                stored_document = await connection.fetchrow(
                    """
                    SELECT client_id, chapter_name, version,
                           content_markdown, content_hash
                    FROM sop_documents
                    WHERE document_id = $1
                    """,
                    document.document_id,
                )
                if stored_document is None or any(
                    stored_document[field] != expected
                    for field, expected in (
                        ("client_id", document.client_id),
                        ("chapter_name", document.chapter_name),
                        ("version", document.version),
                        ("content_markdown", document.content_markdown),
                        ("content_hash", document.content_hash),
                    )
                ):
                    raise ValueError(
                        "an SOP document version cannot be overwritten"
                    )
                for rule in rules:
                    await connection.execute(
                        """
                        INSERT INTO sop_rules (
                            rule_id, document_id, client_id, load_type, scope,
                            load_stage, section_ref, applicable_event_types,
                            priority, version, effective_from, effective_to
                        )
                        VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8::jsonb,
                            $9, $10, $11, $12
                        )
                        """,
                        rule.rule_id,
                        rule.document_id,
                        rule.client_id,
                        rule.load_type,
                        rule.scope,
                        rule.load_stage,
                        rule.section_ref,
                        json.dumps(
                            [event_type.value for event_type in rule.applicable_event_types]
                        ),
                        rule.priority,
                        rule.version,
                        rule.effective_from,
                        rule.effective_to,
                    )

    async def find_applicable_sops(
        self,
        *,
        client_id: str,
        load_type: str | None,
        stage: LoadStage,
        event_type: EventType,
        at: datetime,
    ) -> list[asyncpg.Record]:
        """Return all active standard and matching override candidates."""
        return await self._pool.fetch(
            """
            SELECT r.rule_id, r.document_id, r.scope, r.load_stage,
                   r.client_id, r.load_type, r.section_ref,
                   r.applicable_event_types, r.priority,
                   r.version, r.effective_from, r.effective_to,
                   d.client_id AS document_client_id, d.chapter_name,
                   d.version AS document_version, d.content_markdown,
                   d.content_hash
            FROM sop_rules AS r
            JOIN sop_documents AS d ON d.document_id = r.document_id
            WHERE (
                  (r.scope = 'standard' AND r.client_id IS NULL)
                  OR (r.scope = 'client_override' AND r.client_id = $1
                      AND (r.load_type IS NULL OR r.load_type = $2))
                  OR (r.scope = 'load_type_override' AND r.load_type = $2
                      AND (r.client_id IS NULL OR r.client_id = $1))
              )
              AND (r.load_stage IS NULL OR r.load_stage = $3)
              AND r.applicable_event_types @> to_jsonb(ARRAY[$4::text])
              AND r.effective_from <= $5
              AND (r.effective_to IS NULL OR r.effective_to > $5)
            ORDER BY r.priority DESC, r.rule_id
            """,
            client_id,
            load_type,
            stage,
            event_type,
            at,
        )

    async def find_standard_sop(
        self,
        *,
        stage: LoadStage,
        event_type: EventType,
        at: datetime,
    ) -> list[asyncpg.Record]:
        """Return active standard-SOP rules for compatibility with older callers."""
        return await self._pool.fetch(
            """
            SELECT r.rule_id, r.document_id, r.scope, r.load_stage,
                   r.client_id, r.load_type, r.section_ref,
                   r.applicable_event_types, r.priority, r.version,
                   r.effective_from, r.effective_to,
                   d.client_id AS document_client_id, d.chapter_name,
                   d.version AS document_version, d.content_markdown,
                   d.content_hash
            FROM sop_rules AS r
            JOIN sop_documents AS d ON d.document_id = r.document_id
            WHERE r.scope = 'standard'
              AND r.client_id IS NULL
              AND (r.load_stage IS NULL OR r.load_stage = $1)
              AND r.applicable_event_types @> to_jsonb(ARRAY[$2::text])
              AND r.effective_from <= $3
              AND (r.effective_to IS NULL OR r.effective_to > $3)
            ORDER BY r.priority DESC, r.rule_id
            """,
            stage,
            event_type,
            at,
        )
