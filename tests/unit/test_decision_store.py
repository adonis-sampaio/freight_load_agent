from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.memory.decision_store import DecisionStore
from models.decision import (
    DecisionDisposition,
    DecisionRecord,
    ModelDisposition,
    ProposedAction,
    ReviewStatus,
    ValidationStatus,
)


class FakePool:
    def __init__(self) -> None:
        self.query: str | None = None
        self.args: tuple[Any, ...] = ()

    async def execute(self, query: str, *args: Any) -> str:
        self.query = query
        self.args = args
        return "INSERT 0 1"


def make_decision(**updates: Any) -> DecisionRecord:
    values: dict[str, Any] = {
        "decision_id": "decision-1",
        "broker_id": "broker-1",
        "load_id": "load-1",
        "trigger_event_id": "event-1",
        "disposition": DecisionDisposition.NO_ACTION,
        "validation_status": ValidationStatus.ACCEPTED,
        "created_at": datetime.now(UTC),
    }
    values.update(updates)
    return DecisionRecord(**values)


def test_decision_record_is_immutable_and_rejects_secret_fields() -> None:
    decision = make_decision()

    with pytest.raises(ValidationError):
        decision.disposition = DecisionDisposition.PROPOSE_ACTION

    with pytest.raises(ValidationError):
        make_decision(api_key="must-not-be-stored")


def test_abstention_is_review_metadata_on_a_no_action_record() -> None:
    decision = make_decision(
        model_disposition=ModelDisposition.ABSTAIN,
        review_status=ReviewStatus.REQUIRED,
        review_reason="The message is ambiguous.",
    )

    assert decision.disposition == DecisionDisposition.NO_ACTION
    assert decision.review_status == ReviewStatus.REQUIRED
    assert decision.proposed_actions == ()


def test_abstention_cannot_be_an_execution_authorization() -> None:
    with pytest.raises(ValidationError):
        make_decision(
            disposition=DecisionDisposition.PROPOSE_ACTION,
            model_disposition=ModelDisposition.ABSTAIN,
            review_status=ReviewStatus.REQUIRED,
            review_reason="Ambiguous",
            validation_status=ValidationStatus.ACCEPTED,
            proposed_actions=(
                ProposedAction(action_type="sms_send", payload={"message": "send"}),
            ),
        )


@pytest.mark.asyncio
async def test_decision_store_persists_provenance_without_credentials() -> None:
    pool = FakePool()
    store = DecisionStore(pool)  # type: ignore[arg-type]
    decision = make_decision(
        sop_version="v1",
        sop_content_hash="sha256:abc",
        ruleset_version="rules-1",
        provider_id="provider",
        model_id="model",
        provider_request_id="request-1",
        prompt_schema_version="1",
        validation_reasons=("unrelated",),
    )

    assert await store.record(decision) == decision.decision_id
    assert "provider_request_id" in (pool.query or "")
    assert pool.args[10:13] == ("v1", "sha256:abc", "rules-1")
    assert "must-not-be-stored" not in repr(pool.args)
