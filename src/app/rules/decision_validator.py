"""Deterministic safety checks for model-proposed actions."""

from dataclasses import dataclass

from app.agent.decision_context import DecisionContext
from app.services.contact_validation import (
    InvalidContactError,
    resolve_action_contact,
)
from models.action import ActionType
from models.decision import (
    DecisionDisposition,
    LLMDecisionProposal,
    ModelDisposition,
    ProposedAction,
    ReviewStatus,
    UncertaintyLevel,
    ValidationStatus,
)
from models.contact import Contact, ContactChannel
from models.load import LoadStatus


@dataclass(frozen=True)
class DecisionValidationResult:
    disposition: DecisionDisposition
    validation_status: ValidationStatus
    validation_reasons: tuple[str, ...]
    review_status: ReviewStatus
    review_reason: str | None
    proposed_actions: tuple[ProposedAction, ...]

    @property
    def authorized(self) -> bool:
        return (
            self.validation_status == ValidationStatus.ACCEPTED
            and self.disposition == DecisionDisposition.PROPOSE_ACTION
        )


class DecisionValidator:
    """Validate proposal provenance, evidence, actions, contacts, and state."""

    def validate(
        self,
        proposal: LLMDecisionProposal,
        context: DecisionContext,
    ) -> DecisionValidationResult:
        if proposal.model_disposition == ModelDisposition.NO_ACTION:
            if proposal.load_id != context.load.load_id:
                return DecisionValidationResult(
                    disposition=DecisionDisposition.NO_ACTION,
                    validation_status=ValidationStatus.REJECTED,
                    validation_reasons=("load_id_mismatch",),
                    review_status=ReviewStatus.REQUIRED,
                    review_reason="load_id_mismatch",
                    proposed_actions=(),
                )
            return self._no_action()
        if proposal.model_disposition == ModelDisposition.ABSTAIN:
            if proposal.load_id != context.load.load_id:
                return DecisionValidationResult(
                    disposition=DecisionDisposition.NO_ACTION,
                    validation_status=ValidationStatus.REJECTED,
                    validation_reasons=("load_id_mismatch",),
                    review_status=ReviewStatus.REQUIRED,
                    review_reason="load_id_mismatch",
                    proposed_actions=(),
                )
            reason = proposal.reason_code or "model_abstained"
            if proposal.missing_facts:
                reason = f"{reason}; missing facts: {', '.join(proposal.missing_facts)}"
            return self._no_action(
                review_status=ReviewStatus.REQUIRED,
                review_reason=reason,
            )

        reasons = self._validate_provenance_and_evidence(proposal, context)
        reasons.extend(self._validate_actions(proposal, context))
        if reasons:
            return DecisionValidationResult(
                disposition=DecisionDisposition.NO_ACTION,
                validation_status=ValidationStatus.REJECTED,
                validation_reasons=tuple(dict.fromkeys(reasons)),
                review_status=ReviewStatus.REQUIRED,
                review_reason="; ".join(dict.fromkeys(reasons)),
                proposed_actions=tuple(proposal.proposed_actions),
            )
        return DecisionValidationResult(
            disposition=DecisionDisposition.PROPOSE_ACTION,
            validation_status=ValidationStatus.ACCEPTED,
            validation_reasons=(),
            review_status=ReviewStatus.NOT_REQUIRED,
            review_reason=None,
            proposed_actions=tuple(proposal.proposed_actions),
        )

    @staticmethod
    def _no_action(
        *,
        review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED,
        review_reason: str | None = None,
    ) -> DecisionValidationResult:
        return DecisionValidationResult(
            disposition=DecisionDisposition.NO_ACTION,
            validation_status=ValidationStatus.ACCEPTED,
            validation_reasons=(),
            review_status=review_status,
            review_reason=review_reason,
            proposed_actions=(),
        )

    @staticmethod
    def _validate_provenance_and_evidence(
        proposal: LLMDecisionProposal,
        context: DecisionContext,
    ) -> list[str]:
        errors: list[str] = []
        active_sop = context.llm_request.active_sop
        if proposal.load_id != context.load.load_id:
            errors.append("load_id_mismatch")
        if proposal.sop_document_id != active_sop.document_id:
            errors.append("sop_document_id_mismatch")
        if proposal.sop_version != active_sop.version:
            errors.append("sop_version_mismatch")
        if proposal.sop_content_hash != active_sop.content_hash:
            errors.append("sop_content_hash_mismatch")
        matching_sections = [
            section
            for section in active_sop.sections
            if section.section_ref == proposal.sop_section_ref
        ]
        if not matching_sections:
            errors.append("sop_section_ref_mismatch")
        elif (
            not proposal.supporting_passage
            or proposal.supporting_passage not in matching_sections[0].text
        ):
            errors.append("sop_supporting_passage_mismatch")

        available_facts = context.llm_request.load_context.available_facts
        for evidence in proposal.load_evidence:
            if (
                evidence.fact_id not in available_facts
                or available_facts[evidence.fact_id] != evidence.value
            ):
                errors.append("load_evidence_mismatch")
        return errors

    @classmethod
    def _validate_actions(
        cls,
        proposal: LLMDecisionProposal,
        context: DecisionContext,
    ) -> list[str]:
        errors: list[str] = []
        if proposal.uncertainty == UncertaintyLevel.HIGH:
            errors.append("high_uncertainty_requires_review")
        if proposal.missing_facts:
            errors.append("proposal_has_missing_facts")
        allowed = set(context.llm_request.allowed_action_types)
        contacts = {contact.contact_id: contact for contact in context.contacts}
        for action in proposal.proposed_actions:
            if action.action_type not in allowed:
                errors.append("unsupported_action")
                continue
            if (
                context.load.status != LoadStatus.ACTIVE
                and action.action_type != ActionType.STATUS_UPDATE
            ):
                errors.append("load_state_disallows_action")
            errors.extend(cls._validate_action_payload(action, contacts, context))
        return errors

    @classmethod
    def _validate_action_payload(
        cls,
        action: ProposedAction,
        contacts: dict[str, Contact],
        context: DecisionContext,
    ) -> list[str]:
        payload = action.payload
        if action.action_type == ActionType.SMS_SEND:
            if not isinstance(payload.get("message"), str) or not payload["message"].strip():
                return ["invalid_action_payload"]
            return cls._validate_contact(
                action,
                contacts,
                expected_channel=ContactChannel.SMS,
                context=context,
            )
        if action.action_type == ActionType.EMAIL_SEND:
            if (
                not isinstance(payload.get("thread_id"), str)
                or not isinstance(payload.get("to"), list)
                or not payload["to"]
                or not isinstance(payload.get("subject"), str)
                or not isinstance(payload.get("body"), str)
            ):
                return ["invalid_action_payload"]
            return cls._validate_contact(
                action,
                contacts,
                expected_channel=ContactChannel.EMAIL,
                context=context,
            )
        if action.action_type == ActionType.SLACK_ALERT:
            if not all(
                isinstance(payload.get(key), str) and payload[key].strip()
                for key in ("channel", "message")
            ):
                return ["invalid_action_payload"]
        elif action.action_type == ActionType.HERO_TASK:
            if not all(
                isinstance(payload.get(key), str) and payload[key].strip()
                for key in ("task_type", "summary")
            ):
                return ["invalid_action_payload"]
        elif action.action_type == ActionType.TIMER_SET:
            if not all(
                isinstance(payload.get(key), str) and payload[key].strip()
                for key in ("timer_name", "trigger_at")
            ):
                return ["invalid_action_payload"]
        elif action.action_type == ActionType.TMS_UPDATE:
            if not all(
                isinstance(payload.get(key), str) and payload[key].strip()
                for key in ("status", "note")
            ):
                return ["invalid_action_payload"]
        elif action.action_type == ActionType.STATUS_UPDATE:
            if not isinstance(payload.get("status"), str) or not payload["status"]:
                return ["invalid_action_payload"]
        return []

    @staticmethod
    def _validate_contact(
        action: ProposedAction,
        contacts: dict[str, Contact],
        *,
        expected_channel: ContactChannel,
        context: DecisionContext,
    ) -> list[str]:
        try:
            resolve_action_contact(
                action,
                contacts.values(),
                load_id=context.load.load_id,
                expected_channel=expected_channel,
            )
        except InvalidContactError as error:
            return [error.reason_code]
        return []
