"""Classify document text before any field extraction is accepted."""

import hashlib
import re

from models.document import DocumentAttachment, DocumentStatus, DocumentType


class DocumentClassifier:
    def create_attachment(
        self,
        *,
        document_id: str,
        load_id: str,
        source_uri: str,
        content_type: str,
        content: bytes,
    ) -> DocumentAttachment:
        return DocumentAttachment(
            document_id=document_id,
            load_id=load_id,
            document_type=DocumentType.UNKNOWN,
            source_uri=source_uri,
            content_type=content_type,
            hash=f"sha256:{hashlib.sha256(content).hexdigest()}",
        )

    def classify(
        self,
        attachment: DocumentAttachment,
        text: str,
    ) -> DocumentAttachment:
        normalized = re.sub(r"\s+", " ", text).casefold()
        if "bill of lading" in normalized or re.search(r"\bbol\b", normalized):
            document_type = DocumentType.BOL
        elif "proof of delivery" in normalized or re.search(r"\bpod\b", normalized):
            document_type = DocumentType.POD
        elif "rate confirmation" in normalized:
            document_type = DocumentType.RATE_CONFIRMATION
        elif re.search(r"\breceipt\b", normalized):
            document_type = DocumentType.RECEIPT
        else:
            document_type = DocumentType.UNKNOWN

        return DocumentAttachment.model_validate(
            {
                **attachment.model_dump(),
                "document_type": document_type,
                "status": (
                    DocumentStatus.CLASSIFIED
                    if document_type != DocumentType.UNKNOWN
                    else DocumentStatus.REJECTED
                ),
                "extracted_values_json": {},
                "extraction_version": None,
            }
        )
