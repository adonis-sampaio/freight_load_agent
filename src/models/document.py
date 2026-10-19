"""Load document attachment records."""

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from .common import StrictModel


class DocumentType(StrEnum):
    BOL = "bol"
    POD = "pod"
    RATE_CONFIRMATION = "rate_confirmation"
    RECEIPT = "receipt"
    UNKNOWN = "unknown"


class DocumentStatus(StrEnum):
    RECEIVED = "received"
    CLASSIFIED = "classified"
    EXTRACTED = "extracted"
    REJECTED = "rejected"


class DocumentAttachment(StrictModel):
    document_id: str = Field(min_length=1)
    load_id: str = Field(min_length=1)
    document_type: DocumentType
    source_uri: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    hash: str = Field(min_length=1)
    status: DocumentStatus = DocumentStatus.RECEIVED
    extracted_values_json: dict[str, Any] = Field(default_factory=dict)
    extraction_version: str | None = None

    @model_validator(mode="after")
    def validate_extraction_state(self) -> "DocumentAttachment":
        if self.status == DocumentStatus.EXTRACTED and self.document_type == DocumentType.UNKNOWN:
            raise ValueError("unknown documents cannot have accepted extracted values")
        if self.status == DocumentStatus.EXTRACTED and not self.extraction_version:
            raise ValueError("extracted values require an extraction version")
        if (
            self.status == DocumentStatus.REJECTED
            and self.extracted_values_json
        ):
            raise ValueError("rejected documents cannot retain accepted extracted values")
        return self
