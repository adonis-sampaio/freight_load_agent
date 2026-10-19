"""Extract an allowlisted set of key-value fields from classified documents."""

import re

from models.document import DocumentAttachment, DocumentStatus, DocumentType


class DocumentExtractionError(ValueError):
    """Raised when extraction is not safe or no supported value is found."""


_FIELDS_BY_TYPE: dict[DocumentType, dict[str, str]] = {
    DocumentType.BOL: {
        "bol_number": "bol_number",
        "bol_no": "bol_number",
        "bill_of_lading_number": "bol_number",
        "po_number": "po_number",
        "seal_number": "seal_number",
        "trailer_number": "trailer_number",
    },
    DocumentType.POD: {
        "bol_number": "bol_number",
        "delivery_date": "delivery_date",
        "recipient": "recipient_name",
        "recipient_name": "recipient_name",
    },
    DocumentType.RATE_CONFIRMATION: {
        "load_number": "load_number",
        "rate": "rate",
        "carrier": "carrier_name",
        "carrier_name": "carrier_name",
    },
    DocumentType.RECEIPT: {
        "receipt_number": "receipt_number",
        "amount": "amount",
        "date": "date",
    },
}


class DocumentExtractor:
    def extract(
        self,
        attachment: DocumentAttachment,
        text: str,
        *,
        extraction_version: str,
    ) -> DocumentAttachment:
        if attachment.document_type == DocumentType.UNKNOWN:
            raise DocumentExtractionError(
                "unknown document types cannot be extracted"
            )
        if attachment.status != DocumentStatus.CLASSIFIED:
            raise DocumentExtractionError(
                "document must be classified before extraction"
            )
        if not extraction_version.strip():
            raise ValueError("extraction_version must not be empty")

        fields = _FIELDS_BY_TYPE[attachment.document_type]
        extracted: dict[str, str] = {}
        for line in text.splitlines():
            match = re.match(r"^\s*([A-Za-z][A-Za-z _-]*)\s*:\s*(.*?)\s*$", line)
            if match is None or not match.group(2):
                continue
            key = re.sub(r"[^a-z0-9]+", "_", match.group(1).casefold()).strip("_")
            canonical = fields.get(key)
            if canonical is not None:
                extracted[canonical] = match.group(2)
        if not extracted:
            raise DocumentExtractionError(
                "no supported fields were extracted from the classified document"
            )
        return DocumentAttachment.model_validate(
            {
                **attachment.model_dump(),
                "status": DocumentStatus.EXTRACTED,
                "extracted_values_json": extracted,
                "extraction_version": extraction_version,
            }
        )
