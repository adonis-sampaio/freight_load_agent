import hashlib

import pytest

from app.services.document_classifier import DocumentClassifier
from app.services.document_extractor import (
    DocumentExtractionError,
    DocumentExtractor,
)
from models.document import DocumentAttachment, DocumentStatus, DocumentType


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BILL OF LADING\nBOL Number: BOL-42", DocumentType.BOL),
        ("PROOF OF DELIVERY\nRecipient: Alex", DocumentType.POD),
        ("RATE CONFIRMATION\nRate: $2,400", DocumentType.RATE_CONFIRMATION),
        ("RECEIPT\nReceipt Number: R-9", DocumentType.RECEIPT),
        ("Unrecognized attachment", DocumentType.UNKNOWN),
    ],
)
def test_classifier_identifies_supported_and_unknown_documents(
    text: str,
    expected: DocumentType,
) -> None:
    attachment = make_attachment(text)

    classified = DocumentClassifier().classify(attachment, text)

    assert classified.document_type == expected
    assert classified.status == (
        DocumentStatus.REJECTED
        if expected == DocumentType.UNKNOWN
        else DocumentStatus.CLASSIFIED
    )


def test_bol_extraction_is_versioned_and_reviewable() -> None:
    text = (
        "BILL OF LADING\n"
        "BOL Number: BOL-42\n"
        "PO Number: PO-99\n"
        "Seal Number: SEAL-7\n"
    )
    attachment = DocumentClassifier().classify(make_attachment(text), text)

    extracted = DocumentExtractor().extract(
        attachment,
        text,
        extraction_version="extractor-v1",
    )

    assert extracted.status == DocumentStatus.EXTRACTED
    assert extracted.extraction_version == "extractor-v1"
    assert extracted.extracted_values_json == {
        "bol_number": "BOL-42",
        "po_number": "PO-99",
        "seal_number": "SEAL-7",
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "PROOF OF DELIVERY\nDelivery Date: 2026-10-12\nRecipient Name: Sam",
            {"delivery_date": "2026-10-12", "recipient_name": "Sam"},
        ),
        (
            "RATE CONFIRMATION\nLoad Number: L-77\nRate: $2,400\nCarrier Name: Road Co",
            {
                "load_number": "L-77",
                "rate": "$2,400",
                "carrier_name": "Road Co",
            },
        ),
        (
            "RECEIPT\nReceipt Number: R-9\nAmount: $75.00\nDate: 2026-10-11",
            {
                "receipt_number": "R-9",
                "amount": "$75.00",
                "date": "2026-10-11",
            },
        ),
    ],
)
def test_supported_document_types_extract_allowlisted_values(
    text: str,
    expected: dict[str, str],
) -> None:
    attachment = DocumentClassifier().classify(make_attachment(text), text)

    extracted = DocumentExtractor().extract(
        attachment,
        text,
        extraction_version="extractor-v1",
    )

    assert extracted.extracted_values_json == expected


def test_unknown_document_cannot_be_extracted() -> None:
    text = "Unrecognized attachment"
    classified = DocumentClassifier().classify(make_attachment(text), text)

    with pytest.raises(DocumentExtractionError, match="unknown"):
        DocumentExtractor().extract(
            classified,
            text,
            extraction_version="extractor-v1",
        )


def make_attachment(text: str) -> DocumentAttachment:
    return DocumentAttachment(
        document_id="doc-1",
        load_id="load-1",
        document_type=DocumentType.UNKNOWN,
        source_uri="memory://doc-1",
        content_type="text/plain",
        hash=f"sha256:{hashlib.sha256(text.encode()).hexdigest()}",
    )
