"""Normalized LLM proposal and decision-context records."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from .action import ActionType
from .common import EventChannel, EventType, LoadStage, StrictModel


class ModelDisposition(StrEnum):
    PROPOSE_ACTION = "propose_action"
    NO_ACTION = "no_action"
    ABSTAIN = "abstain"


class UncertaintyLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SOPSection(StrictModel):
    section_ref: str = Field(min_length=1)
    text: str = Field(min_length=1)


class DecisionEvent(StrictModel):
    event_id: str = Field(min_length=1)
    event_type: EventType
    source_channel: EventChannel
    source_contact_id: str | None = None
    payload: dict[str, Any]
    occurred_at: AwareDatetime


class DecisionOpenRequest(StrictModel):
    request_id: str = Field(min_length=1)
    request_type: str = Field(min_length=1)
    state: Literal["open"]


class DecisionLoadContext(StrictModel):
    load_id: str = Field(min_length=1)
    current_stage: LoadStage
    available_facts: dict[str, Any] = Field(default_factory=dict)
    open_requests: list[DecisionOpenRequest] = Field(default_factory=list)
    recent_events: list[DecisionEvent] = Field(default_factory=list)


class ActiveSOP(StrictModel):
    document_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    sections: list[SOPSection] = Field(min_length=1)


class LLMDecisionRequest(StrictModel):
    schema_version: Literal["1"] = "1"
    max_output_tokens: int = Field(default=2048, gt=0)
    event: DecisionEvent
    load_context: DecisionLoadContext
    active_sop: ActiveSOP
    allowed_action_types: list[ActionType] = Field(min_length=1)


class LoadEvidence(StrictModel):
    fact_id: str = Field(min_length=1)
    value: Any


class ProposedAction(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_type: ActionType
    contact_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class LLMDecisionProposal(StrictModel):
    schema_version: Literal["1"]
    model_disposition: ModelDisposition
    load_id: str = Field(min_length=1)
    sop_document_id: str | None = None
    sop_version: str | None = None
    sop_content_hash: str | None = None
    sop_section_ref: str | None = None
    supporting_passage: str | None = None
    load_evidence: list[LoadEvidence] = Field(default_factory=list)
    reason_code: str = Field(min_length=1)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    uncertainty: UncertaintyLevel

    @model_validator(mode="after")
    def validate_disposition_actions(self) -> "LLMDecisionProposal":
        if self.model_disposition in (
            ModelDisposition.NO_ACTION,
            ModelDisposition.ABSTAIN,
        ) and self.proposed_actions:
            raise ValueError(
                "no_action and abstain dispositions must not propose actions"
            )
        if (
            self.model_disposition == ModelDisposition.PROPOSE_ACTION
            and not self.proposed_actions
        ):
            raise ValueError("propose_action disposition requires at least one action")
        return self


class DecisionDisposition(StrEnum):
    PROPOSE_ACTION = "propose_action"
    NO_ACTION = "no_action"


class ReviewStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    RESOLVED = "resolved"


class ValidationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROVIDER_FAILED = "provider_failed"
    BUDGET_EXCEEDED = "budget_exceeded"


class DecisionRecord(StrictModel):
    """Immutable audit record; it never grants execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(min_length=1)
    broker_id: str = Field(min_length=1)
    load_id: str = Field(min_length=1)
    trigger_event_id: str = Field(min_length=1)
    sop_rule_id: str | None = None
    disposition: DecisionDisposition | None = None
    model_disposition: ModelDisposition | None = None
    review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    review_reason: str | None = None
    sop_document_id: str | None = None
    sop_version: str | None = None
    sop_content_hash: str | None = None
    ruleset_version: str | None = None
    sop_section_ref: str | None = None
    supporting_passage: str | None = None
    load_evidence_refs: tuple[LoadEvidence, ...] = ()
    proposed_actions: tuple[ProposedAction, ...] = ()
    provider_id: str | None = None
    model_id: str | None = None
    provider_request_id: str | None = None
    prompt_schema_version: str | None = None
    uncertainty_level: UncertaintyLevel | None = None
    validation_status: ValidationStatus
    validation_reasons: tuple[str, ...] = ()
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_decision_integrity(self) -> "DecisionRecord":
        if (
            self.disposition is None
            and self.validation_status != ValidationStatus.PROVIDER_FAILED
        ):
            raise ValueError("only provider failures may omit system disposition")
        if self.disposition == DecisionDisposition.NO_ACTION and self.proposed_actions:
            rejected_model_actions = (
                self.model_disposition == ModelDisposition.PROPOSE_ACTION
                and self.validation_status == ValidationStatus.REJECTED
            )
            if not rejected_model_actions:
                raise ValueError(
                    "only rejected model proposals may be retained on no_action records"
                )
        if self.model_disposition in (
            ModelDisposition.NO_ACTION,
            ModelDisposition.ABSTAIN,
        ) and self.proposed_actions:
            raise ValueError(
                "no_action and abstain model dispositions cannot contain actions"
            )
        if (
            self.validation_status != ValidationStatus.ACCEPTED
            and self.disposition == DecisionDisposition.PROPOSE_ACTION
        ):
            raise ValueError("unaccepted proposals cannot authorize system actions")
        if (
            self.validation_status == ValidationStatus.ACCEPTED
            and self.disposition == DecisionDisposition.PROPOSE_ACTION
            and not self.proposed_actions
        ):
            raise ValueError("accepted action decisions require proposed actions")
        if self.model_disposition == ModelDisposition.ABSTAIN and (
            self.disposition != DecisionDisposition.NO_ACTION
            or self.review_status != ReviewStatus.REQUIRED
        ):
            raise ValueError(
                "model abstention requires a no_action decision and human review"
            )
        if (
            self.review_status == ReviewStatus.REQUIRED
            and not self.review_reason
        ):
            raise ValueError("required review must include a reason")
        return self
