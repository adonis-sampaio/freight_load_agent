"""Durable open-request repository with guarded lifecycle transitions."""

import re
from datetime import datetime

import asyncpg

from models.event import OpenRequest, RequestState


class RequestTransitionError(ValueError):
    """Raised when a request cannot make the requested state transition."""


class RequestStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, broker_id: str, request: OpenRequest) -> str:
        if request.state != RequestState.OPEN:
            raise RequestTransitionError("new requests must start in the open state")
        await self._pool.execute(
            """
            INSERT INTO open_requests (
                request_id, broker_id, load_id, request_type, question_text,
                state, reminder_count, created_at, expires_at, resolved_at,
                source_contact_id, last_prompted_at, correlation_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            request.request_id,
            broker_id,
            request.load_id,
            request.request_type,
            request.question_text,
            request.state,
            request.reminder_count,
            request.created_at,
            request.expires_at,
            request.resolved_at,
            request.source_contact_id,
            request.last_prompted_at,
            request.correlation_id,
        )
        return request.request_id

    async def get_open_requests(
        self, broker_id: str, load_id: str
    ) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT request_id, broker_id, load_id, request_type, question_text,
                   state, reminder_count, created_at, expires_at, resolved_at,
                   source_contact_id, last_prompted_at, correlation_id
            FROM open_requests
            WHERE broker_id = $1 AND load_id = $2 AND state = 'open'
            ORDER BY created_at
            """,
            broker_id,
            load_id,
        )

    async def transition(
        self,
        broker_id: str,
        load_id: str,
        request_id: str,
        new_state: RequestState,
    ) -> None:
        if new_state == RequestState.OPEN:
            raise RequestTransitionError(
                "closed requests cannot be reopened; create a new request"
            )
        result = await self._pool.execute(
            """
            UPDATE open_requests
            SET state = $4, resolved_at = now()
            WHERE broker_id = $1 AND load_id = $2
              AND request_id = $3 AND state = 'open'
              AND (
                  $5::boolean = FALSE
                  OR (expires_at IS NOT NULL AND expires_at <= now())
              )
            """,
            broker_id,
            load_id,
            request_id,
            new_state,
            new_state == RequestState.EXPIRED,
        )
        if result != "UPDATE 1":
            raise RequestTransitionError(
                f"request {request_id} is missing or is no longer open"
            )

    async def get_open_request(
        self, broker_id: str, load_id: str, request_id: str
    ) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            """
            SELECT request_id, broker_id, load_id, request_type, question_text,
                   state, reminder_count, created_at, expires_at, resolved_at,
                   source_contact_id, last_prompted_at, correlation_id
            FROM open_requests
            WHERE broker_id = $1 AND load_id = $2
              AND request_id = $3 AND state = 'open'
            """,
            broker_id,
            load_id,
            request_id,
        )

    async def increment_reminder(
        self,
        broker_id: str,
        load_id: str,
        request_id: str,
        *,
        maximum_reminders: int = 2,
    ) -> int:
        if maximum_reminders < 1 or maximum_reminders > 10:
            raise ValueError("maximum_reminders must be between 1 and 10")
        row = await self._pool.fetchrow(
            """
            UPDATE open_requests
            SET reminder_count = reminder_count + 1, last_prompted_at = now()
            WHERE broker_id = $1 AND load_id = $2
              AND request_id = $3 AND state = 'open'
              AND reminder_count < $4
            RETURNING reminder_count
            """,
            broker_id,
            load_id,
            request_id,
            maximum_reminders,
        )
        if row is None:
            raise RequestTransitionError(
                f"request {request_id} is closed or reached its reminder limit"
            )
        return int(row["reminder_count"])

    async def resolve_answer(
        self,
        broker_id: str,
        load_id: str,
        request_id: str,
        answer: str,
        *,
        explicitly_answers_question: bool = False,
    ) -> bool:
        request = await self.get_open_request(broker_id, load_id, request_id)
        if request is None:
            return False
        is_eta_request = request["request_type"] == "eta_request"
        if is_eta_request and not (
            is_usable_eta(answer) or explicitly_answers_question
        ):
            return False
        if not is_eta_request and not explicitly_answers_question:
            return False
        await self.transition(
            broker_id,
            load_id,
            request_id,
            RequestState.ANSWERED,
        )
        return True

    async def reopen(
        self,
        broker_id: str,
        previous_request_id: str,
        new_request: OpenRequest,
        *,
        remains_relevant: bool,
        sop_requires_reopen: bool,
    ) -> str:
        if not remains_relevant or not sop_requires_reopen:
            raise RequestTransitionError(
                "a request may be reopened only when still relevant and required by SOP"
            )
        previous = await self._pool.fetchrow(
            """
            SELECT load_id, request_type, state
            FROM open_requests
            WHERE broker_id = $1 AND request_id = $2
            """,
            broker_id,
            previous_request_id,
        )
        if previous is None or previous["state"] != RequestState.EXPIRED:
            raise RequestTransitionError(
                "only an expired request may be reopened"
            )
        if (
            previous["load_id"] != new_request.load_id
            or previous["request_type"] != new_request.request_type
        ):
            raise RequestTransitionError(
                "reopened request must match the prior load and request type"
            )
        return await self.create(broker_id, new_request)


_ETA_TIME = re.compile(
    r"\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
_ETA_DURATION = re.compile(
    r"\b(?:in\s+)?\d+\s*(?:minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)


def is_usable_eta(answer: str) -> bool:
    """Accept explicit clock times, ISO timestamps, or relative durations only."""
    text = answer.strip()
    if not text:
        return False
    if _ETA_TIME.search(text) or _ETA_DURATION.search(text):
        return True
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None
