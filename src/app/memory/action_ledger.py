"""Durable outbound-action ledger repository."""

import json

import asyncpg

from models.action import ActionStatus, LoadAction


class ActionLedger:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(self, broker_id: str, action: LoadAction) -> str:
        await self._pool.execute(
            """
            INSERT INTO load_actions (
                action_id, broker_id, load_id, action_type, status,
                trigger_event_id, sop_rule_id, actor, payload_json,
                correlation_id, external_id, failure_reason, created_at, completed_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb,
                $10, $11, $12, $13, $14
            )
            """,
            action.action_id,
            broker_id,
            action.load_id,
            action.action_type,
            action.status,
            action.trigger_event_id,
            action.sop_rule_id,
            action.actor,
            json.dumps(action.payload_json),
            action.correlation_id,
            action.external_id,
            action.failure_reason,
            action.created_at,
            action.completed_at,
        )
        return action.action_id

    async def update_status(
        self,
        broker_id: str,
        load_id: str,
        action_id: str,
        status: ActionStatus,
        external_id: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        is_terminal = status in {
            ActionStatus.FAILED,
            ActionStatus.DELIVERED,
            ActionStatus.COMPLETED,
        }
        result = await self._pool.execute(
            """
            UPDATE load_actions
            SET status = $4,
                completed_at = CASE WHEN $5 THEN now() ELSE NULL END,
            external_id = COALESCE($6, external_id),
            failure_reason = COALESCE($7, failure_reason)
            WHERE broker_id = $1 AND load_id = $2 AND action_id = $3
            """,
            broker_id,
            load_id,
            action_id,
            status,
            is_terminal,
            external_id,
            failure_reason,
        )
        if result != "UPDATE 1":
            raise LookupError(f"action {action_id} was not found")
