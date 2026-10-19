"""LLM-backed SOP interpretation with strict response normalization."""

from pydantic import ValidationError

from adapters.llm_provider_adapter import (
    InvalidProviderResponseError,
    LLMInputLimitExceeded,
    LLMProviderAdapter,
    estimate_input_tokens,
    request_proposal,
)
from config.settings import Settings
from models.decision import LLMDecisionProposal, LLMDecisionRequest


class LLMSOPInterpreter:
    def __init__(
        self,
        provider: LLMProviderAdapter,
        settings: Settings,
    ) -> None:
        self._provider = provider
        self._settings = settings

    async def interpret(
        self, request: LLMDecisionRequest
    ) -> LLMDecisionProposal:
        """Return only a proposal validated against the internal schema."""
        input_tokens = estimate_input_tokens(request)
        if input_tokens > self._settings.llm_max_input_tokens:
            raise LLMInputLimitExceeded(
                "decision request exceeds the configured input-token limit"
            )
        bounded_request = request.model_copy(
            update={"max_output_tokens": self._settings.llm_max_output_tokens}
        )
        response = await request_proposal(
            self._provider,
            bounded_request,
            self._settings,
        )
        try:
            normalized = (
                response.model_dump(mode="python")
                if isinstance(response, LLMDecisionProposal)
                else response
            )
            return LLMDecisionProposal.model_validate(normalized)
        except ValidationError as error:
            raise InvalidProviderResponseError(
                "LLM provider returned an invalid decision proposal"
            ) from error
