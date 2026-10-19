import json
from pathlib import Path
from typing import Any

from app.rules.decision_validator import DecisionValidator
from models.decision import (
    DecisionDisposition,
    ModelDisposition,
    ProposedAction,
    ReviewStatus,
    ValidationStatus,
)
from tests.integration.agent_support import make_context, proposal

FIXTURE = Path(__file__).with_name("scenarios.json")
ETA_PASSAGE = "When delay risk is high, request an ETA from the driver by SMS"


def test_representative_decision_scenarios_meet_safety_metrics() -> None:
    scenarios: list[dict[str, Any]] = json.loads(FIXTURE.read_text())
    outcomes = []
    for scenario in scenarios:
        action = ProposedAction(
            action_type="sms_send",
            contact_id="driver-1",
            payload={
                "message": "What is your ETA?",
                "request_type": "eta_request",
                "operational_step": "eta",
            },
        )
        model_disposition = ModelDisposition(scenario["model_disposition"])
        passage = (
            "stale SOP passage"
            if scenario["name"] == "stale_sop_citation"
            else ETA_PASSAGE
        )
        candidate = proposal(
            reason_code=scenario["name"],
            passage=passage,
            actions=(
                [action.model_dump()]
                if model_disposition == ModelDisposition.PROPOSE_ACTION
                else []
            ),
            disposition=model_disposition,
        )
        result = DecisionValidator().validate(
            candidate,
            make_context(
                scenario["name"],
                event_type="ping",
            ),
        )
        outcomes.append((scenario, result))

    required = [
        result
        for scenario, result in outcomes
        if scenario["expected_disposition"] == "propose_action"
    ]
    no_action = [
        result
        for scenario, result in outcomes
        if scenario["expected_disposition"] == "no_action"
    ]
    assert len(required) == 1
    assert all(
        result.disposition.value == scenario["expected_disposition"]
        and result.validation_status.value == scenario["expected_validation"]
        and result.review_status.value == scenario["expected_review"]
        for scenario, result in outcomes
    )
    false_positive_rate = sum(
        result.disposition == DecisionDisposition.PROPOSE_ACTION
        for result in no_action
    ) / len(no_action)
    citation_accuracy = sum(
        ("sop_supporting_passage_mismatch" not in result.validation_reasons)
        == (scenario["name"] != "stale_sop_citation")
        for scenario, result in outcomes
    ) / len(outcomes)

    assert false_positive_rate == 0.0
    assert citation_accuracy == 1.0
    assert required[0].validation_status == ValidationStatus.ACCEPTED
