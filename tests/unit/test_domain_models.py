from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from adapters.llm_provider_adapter import LLMProviderAdapter
from models.contact import Contact
from models.decision import (
    LLMDecisionProposal,
    LLMDecisionRequest,
    ModelDisposition,
    ProposedAction,
    UncertaintyLevel,
)
from models.document import DocumentAttachment, DocumentStatus, DocumentType
from models.event import OpenRequest, RequestState, RequestType
from models.load import Load, Stop


def test_load_rejects_delivery_before_pickup() -> None:
    with pytest.raises(ValidationError, match="must follow a pickup"):
        Load(
            load_id="load-1",
            broker_id="broker-1",
            broker_name="Broker",
            current_stage="dispatched",
            stops=[
                Stop(stop_id="delivery", sequence=1, type="delivery"),
                Stop(stop_id="pickup", sequence=2, type="pickup"),
            ],
        )


def test_load_rejects_duplicate_stop_sequence() -> None:
    with pytest.raises(ValidationError, match="sequence must be unique"):
        Load(
            load_id="load-1",
            broker_id="broker-1",
            broker_name="Broker",
            current_stage="at_pickup",
            stops=[
                Stop(stop_id="pickup", sequence=1, type="pickup"),
                Stop(stop_id="delivery", sequence=1, type="delivery"),
            ],
        )


def test_contact_primary_channel_must_be_available() -> None:
    with pytest.raises(ValidationError, match="must be present in channels"):
        Contact(
            contact_id="driver-1",
            load_id="load-1",
            role="driver",
            channels=["sms"],
            external_ref="+15551234567",
            primary_channels={"eta": "email"},
        )


def test_closed_request_requires_resolution_timestamp() -> None:
    with pytest.raises(ValidationError, match="must have resolved_at"):
        OpenRequest(
            request_id="request-1",
            load_id="load-1",
            request_type=RequestType.ETA,
            question_text="What is your ETA?",
            state=RequestState.ANSWERED,
        )


def test_non_action_proposal_cannot_include_actions() -> None:
    with pytest.raises(ValidationError, match="must not propose actions"):
        LLMDecisionProposal(
            schema_version="1",
            model_disposition=ModelDisposition.ABSTAIN,
            load_id="load-1",
            reason_code="unclear",
            uncertainty=UncertaintyLevel.HIGH,
            proposed_actions=[
                ProposedAction(action_type="sms_send", payload={"message": "send"})
            ],
        )


def test_unknown_attachment_cannot_be_marked_extracted() -> None:
    with pytest.raises(ValidationError, match="unknown documents"):
        DocumentAttachment(
            document_id="doc-1",
            load_id="load-1",
            document_type=DocumentType.UNKNOWN,
            source_uri="s3://bucket/doc.pdf",
            content_type="application/pdf",
            hash="sha256:abc",
            status=DocumentStatus.EXTRACTED,
            extraction_version="v1",
        )


def test_open_request_accepts_timezone_aware_created_at() -> None:
    request = OpenRequest(
        request_id="request-1",
        load_id="load-1",
        request_type="eta_request",
        question_text="What is your ETA?",
        created_at=datetime.now(UTC),
    )

    assert request.state == RequestState.OPEN


def test_provider_adapter_protocol_is_mockable() -> None:
    class MockProvider:
        async def propose(
            self, request: LLMDecisionRequest
        ) -> LLMDecisionProposal:
            raise NotImplementedError

    assert isinstance(MockProvider(), LLMProviderAdapter)
