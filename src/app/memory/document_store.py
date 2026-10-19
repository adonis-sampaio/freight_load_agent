"""Durable storage for received and classified document attachments."""

import json

import asyncpg

from models.document import (
    DocumentAttachment,
    DocumentStatus,
    DocumentType,
)


class DocumentStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_received(
        self,
        broker_id: str,
        attachment: DocumentAttachment,
    ) -> str:
        if attachment.status != DocumentStatus.RECEIVED:
            raise ValueError("new document attachments must be received first")
        await self._pool.execute(
            """
            INSERT INTO document_attachments (
                document_id, broker_id, load_id, document_type, source_uri,
                content_type, hash, status, extracted_values_json,
                extraction_version
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
            """,
            attachment.document_id,
            broker_id,
            attachment.load_id,
            attachment.document_type,
            attachment.source_uri,
            attachment.content_type,
            attachment.hash,
            attachment.status,
            json.dumps(attachment.extracted_values_json),
            attachment.extraction_version,
        )
        return attachment.document_id

    async def update_classification(
        self,
        broker_id: str,
        attachment: DocumentAttachment,
    ) -> None:
        if attachment.status not in {
            DocumentStatus.CLASSIFIED,
            DocumentStatus.REJECTED,
        }:
            raise ValueError("classification must produce classified or rejected status")
        result = await self._pool.execute(
            """
            UPDATE document_attachments
            SET document_type = $4, status = $5
            WHERE broker_id = $1 AND load_id = $2 AND document_id = $3
              AND status = 'received'
            """,
            broker_id,
            attachment.load_id,
            attachment.document_id,
            attachment.document_type,
            attachment.status,
        )
        if result != "UPDATE 1":
            raise LookupError("document is missing or already classified")

    async def update_extraction(
        self,
        broker_id: str,
        attachment: DocumentAttachment,
    ) -> None:
        if attachment.status != DocumentStatus.EXTRACTED:
            raise ValueError("extraction update requires extracted status")
        result = await self._pool.execute(
            """
            UPDATE document_attachments
            SET status = $4, extracted_values_json = $5::jsonb,
                extraction_version = $6
            WHERE broker_id = $1 AND load_id = $2 AND document_id = $3
              AND status = 'classified' AND document_type <> $7
            """,
            broker_id,
            attachment.load_id,
            attachment.document_id,
            attachment.status,
            json.dumps(attachment.extracted_values_json),
            attachment.extraction_version,
            DocumentType.UNKNOWN,
        )
        if result != "UPDATE 1":
            raise LookupError("document is missing, unknown, or not classified")
