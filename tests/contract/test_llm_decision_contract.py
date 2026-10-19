import pytest
from pydantic import ValidationError

from datetime import UTC, datetime

from app.agent.decision_context import DecisionContext
from app.rules.decision_validator import DecisionValidator
from models.common import EventChannel, EventType
from models.contact import Contact
from models.decision import (
    DecisionDisposition,
    LLMDecisionProposal,
    ModelDisposition,
    UncertaintyLevel,
    ValidationStatus,
)
from models.event import LoadEvent
from models.load import Load
from models.sop import SOPDocument, SOPRule, SOPScope

PASSAGE = "When delay risk is high, request an ETA from the driver."


def make_context() -> DecisionContext:
    now = datetime.now(UTC)
    return DecisionContext(
        broker_id="broker-1",
        event=LoadEvent(
            event_id="event-1",
            load_id="load-1",
            event_type=EventType.PING,
            source_channel=EventChannel.SYSTEM,
            payload_json={"latitude": 47.6},
            occurred_at=now,
        ),
        load=Load(
            load_id="load-1",
            broker_id="broker-1",
            broker_name="Broker",
            current_stage="at_pickup",
            available_facts={"po_number": "PO-123"},
        ),
        active_rule=SOPRule(
            rule_id="rule-1",
            document_id="standard",
            scope=SOPScope.STANDARD,
            load_stage="at_pickup",
            section_ref="pickup.delay",
            applicable_event_types=["ping"],
            version="v1",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        active_document=SOPDocument(
            document_id="standard",
            chapter_name="Standard",
            version="v1",
            content_markdown=f"## pickup.delay\n{PASSAGE}",
            content_hash="sha256:active",
        ),
        contacts=(
            Contact(
                contact_id="driver-1",
                load_id="load-1",
                role="driver",
                channels=["sms"],
                primary_channels={"eta": "sms"},
                external_ref="+15551234567",
            ),
        ),
        llm_request={
            "event": {
                "event_id": "event-1",
                "event_type": "ping",
                "source_channel": "system",
                "payload": {"latitude": 47.6},
                "occurred_at": now,
            },
            "load_context": {
                "load_id": "load-1",
                "current_stage": "at_pickup",
                "available_facts": {"po_number": "PO-123"},
            },
            "active_sop": {
                "document_id": "standard",
                "version": "v1",
                "content_hash": "sha256:active",
                "sections": [
                    {"section_ref": "pickup.delay", "text": PASSAGE}
                ],
            },
            "allowed_action_types": ["sms_send"],
        },
    )


def make_action_proposal(**updates: object) -> LLMDecisionProposal:
    values: dict[str, object] = {
        "schema_version": "1",
        "model_disposition": "propose_action",
        "load_id": "load-1",
        "sop_document_id": "standard",
        "sop_version": "v1",
        "sop_content_hash": "sha256:active",
        "sop_section_ref": "pickup.delay",
        "supporting_passage": PASSAGE,
        "load_evidence": [{"fact_id": "po_number", "value": "PO-123"}],
        "reason_code": "high_delay_risk",
        "proposed_actions": [
            {
                "action_type": "sms_send",
                "contact_id": "driver-1",
                "payload": {
                    "message": "What is your ETA?",
                    "request_type": "eta_request",
                },
            }
        ],
        "uncertainty": "low",
    }
    values.update(updates)
    return LLMDecisionProposal.model_validate(values)


def test_normalized_proposal_accepts_catalogued_action_and_citation() -> None:
    proposal = LLMDecisionProposal.model_validate(
        {
            "schema_version": "1",
            "model_disposition": "propose_action",
            "load_id": "load-1",
            "sop_document_id": "standard",
            "sop_version": "v1",
            "sop_content_hash": "sha256:abc",
            "sop_section_ref": "pickup.delay",
            "supporting_passage": "Request an ETA when delay risk is high.",
            "reason_code": "delay_risk_high",
            "proposed_actions": [
                {
                    "action_type": "sms_send",
                    "contact_id": "driver-1",
                    "payload": {"message": "What is your ETA?"},
                }
            ],
            "uncertainty": "low",
        }
    )

    assert proposal.model_disposition == ModelDisposition.PROPOSE_ACTION
    assert proposal.proposed_actions[0].action_type == "sms_send"


@pytest.mark.parametrize(
    "fields",
    [
        {"schema_version": "2"},
        {"model_disposition": "execute_tool"},
        {"proposed_actions": [{"action_type": "run_sql", "payload": {}}]},
        {"unexpected_instruction": "ignore the SOP"},
    ],
)
def test_normalized_proposal_rejects_invalid_schema_fields(
    fields: dict[str, object],
) -> None:
    proposal = {
        "schema_version": "1",
        "model_disposition": "no_action",
        "load_id": "load-1",
        "reason_code": "unrelated",
        "uncertainty": "low",
        **fields,
    }

    with pytest.raises(ValidationError):
        LLMDecisionProposal.model_validate(proposal)


def test_abstention_is_not_an_action_disposition() -> None:
    proposal = LLMDecisionProposal(
        schema_version="1",
        model_disposition=ModelDisposition.ABSTAIN,
        load_id="load-1",
        reason_code="missing_context",
        uncertainty=UncertaintyLevel.HIGH,
    )

    assert proposal.proposed_actions == []


def test_stale_sop_citation_is_rejected() -> None:
    result = DecisionValidator().validate(
        make_action_proposal(sop_content_hash="sha256:stale"),
        make_context(),
    )

    assert result.disposition == DecisionDisposition.NO_ACTION
    assert result.validation_status == ValidationStatus.REJECTED
    assert "sop_content_hash_mismatch" in result.validation_reasons


def test_invalid_cited_load_fact_is_rejected() -> None:
    result = DecisionValidator().validate(
        make_action_proposal(
            load_evidence=[{"fact_id": "po_number", "value": "PO-FAKE"}]
        ),
        make_context(),
    )

    assert result.validation_status == ValidationStatus.REJECTED
    assert "load_evidence_mismatch" in result.validation_reasons


def test_supporting_passage_must_be_exact_source_text() -> None:
    result = DecisionValidator().validate(
        make_action_proposal(
            supporting_passage="The LLM's paraphrase of the ETA instruction."
        ),
        make_context(),
    )

    assert result.validation_status == ValidationStatus.REJECTED
    assert "sop_supporting_passage_mismatch" in result.validation_reasons


def test_contact_must_use_primary_operational_channel() -> None:
    result = DecisionValidator().validate(
        make_action_proposal(
            proposed_actions=[
                {
                    "action_type": "sms_send",
                    "contact_id": "driver-1",
                    "payload": {
                        "message": "What is your ETA?",
                        "request_type": "eta_request",
                        "operational_step": "general",
                    },
                }
            ]
        ),
        make_context(),
    )

    assert result.validation_status == ValidationStatus.REJECTED
    assert "contact_primary_channel_mismatch" in result.validation_reasons


def test_model_no_action_with_wrong_load_id_is_rejected() -> None:
    proposal = LLMDecisionProposal(
        schema_version="1",
        model_disposition=ModelDisposition.NO_ACTION,
        load_id="different-load",
        reason_code="unrelated",
        uncertainty=UncertaintyLevel.LOW,
    )

    result = DecisionValidator().validate(proposal, make_context())

    assert result.validation_status == ValidationStatus.REJECTED
    assert "load_id_mismatch" in result.validation_reasons


def test_no_action_and_abstention_are_explicit_system_no_action() -> None:
    context = make_context()
    abstention = LLMDecisionProposal(
        schema_version="1",
        model_disposition="abstain",
        load_id="load-1",
        reason_code="message_unclear",
        uncertainty=UncertaintyLevel.HIGH,
    )

    result = DecisionValidator().validate(abstention, context)

    assert result.disposition == DecisionDisposition.NO_ACTION
    assert result.validation_status == ValidationStatus.ACCEPTED
    assert result.review_status.value == "required"
    assert not result.proposed_actions
