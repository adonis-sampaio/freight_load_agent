"""Append-only persistence for immutable decision records."""

import json
from decimal import Decimal

import asyncpg

from models.decision import DecisionRecord


class LLMUsageBudgetExceededError(RuntimeError):
    """Raised when a load reaches its durable LLM call or cost budget."""


class DecisionStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(self, decision: DecisionRecord) -> str:
        await self._pool.execute(
            """
            INSERT INTO decision_records (
                decision_id, broker_id, load_id, trigger_event_id, sop_rule_id,
                disposition, model_disposition, review_status, review_reason,
                sop_document_id, sop_version, sop_content_hash, ruleset_version,
                sop_section_ref, supporting_passage, load_evidence_refs,
                proposed_actions, provider_id, model_id, provider_request_id,
                prompt_schema_version, uncertainty_level, validation_status,
                validation_reasons, created_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                $14, $15, $16::jsonb, $17::jsonb, $18, $19, $20, $21, $22,
                $23, $24::jsonb, $25
            )
            """,
            decision.decision_id,
            decision.broker_id,
            decision.load_id,
            decision.trigger_event_id,
            decision.sop_rule_id,
            decision.disposition,
            decision.model_disposition,
            decision.review_status,
            decision.review_reason,
            decision.sop_document_id,
            decision.sop_version,
            decision.sop_content_hash,
            decision.ruleset_version,
            decision.sop_section_ref,
            decision.supporting_passage,
            json.dumps(
                [item.model_dump(mode="json") for item in decision.load_evidence_refs]
            ),
            json.dumps(
                [item.model_dump(mode="json") for item in decision.proposed_actions]
            ),
            decision.provider_id,
            decision.model_id,
            decision.provider_request_id,
            decision.prompt_schema_version,
            decision.uncertainty_level,
            decision.validation_status,
            json.dumps(list(decision.validation_reasons)),
            decision.created_at,
        )
        return decision.decision_id

    async def reserve_llm_call(
        self,
        broker_id: str,
        load_id: str,
        *,
        input_tokens: int,
        reserved_output_tokens: int,
        reserved_cost_usd: float,
        maximum_calls: int,
        maximum_cost_usd: float,
    ) -> int:
        row = await self._pool.fetchrow(
            """
            INSERT INTO llm_usage_budgets (
                broker_id, load_id, call_count, reserved_cost_usd,
                estimated_input_tokens, reserved_output_tokens,
                total_latency_ms
            )
            SELECT $1, $2, 1, $5, $3, $4, 0
            WHERE $5 <= $7
            ON CONFLICT (broker_id, load_id)
            DO UPDATE SET
                call_count = llm_usage_budgets.call_count + 1,
                reserved_cost_usd =
                    llm_usage_budgets.reserved_cost_usd + EXCLUDED.reserved_cost_usd,
                estimated_input_tokens =
                    llm_usage_budgets.estimated_input_tokens
                    + EXCLUDED.estimated_input_tokens,
                reserved_output_tokens =
                    llm_usage_budgets.reserved_output_tokens
                    + EXCLUDED.reserved_output_tokens,
                updated_at = now()
            WHERE llm_usage_budgets.call_count < $6
              AND llm_usage_budgets.reserved_cost_usd + EXCLUDED.reserved_cost_usd <= $7
            RETURNING call_count
            """,
            broker_id,
            load_id,
            input_tokens,
            reserved_output_tokens,
            Decimal(str(reserved_cost_usd)),
            maximum_calls,
            Decimal(str(maximum_cost_usd)),
        )
        if row is None:
            raise LLMUsageBudgetExceededError(
                "per-load LLM call or cost budget is exhausted"
            )
        return int(row["call_count"])

    async def record_llm_latency(
        self,
        broker_id: str,
        load_id: str,
        latency_ms: int,
    ) -> None:
        result = await self._pool.execute(
            """
            UPDATE llm_usage_budgets
            SET last_latency_ms = $3,
                total_latency_ms = total_latency_ms + $3
            WHERE broker_id = $1 AND load_id = $2
            """,
            broker_id,
            load_id,
            latency_ms,
        )
        if result != "UPDATE 1":
            raise LookupError("LLM usage budget reservation was not found")
