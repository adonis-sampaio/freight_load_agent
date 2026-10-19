from typing import Any

import pytest

from app.memory.request_store import (
    RequestStore,
    RequestTransitionError,
    is_usable_eta,
)
from models.event import OpenRequest, RequestState, RequestType


class FakePool:
    def __init__(
        self,
        *,
        fetchrow_result: dict[str, Any] | None = None,
        execute_result: str = "UPDATE 1",
    ) -> None:
        self.fetchrow_result = fetchrow_result
        self.execute_result = execute_result
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self.fetchrow_result

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        return self.execute_result


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("ETA 10:30 AM", True),
        ("I will arrive in 45 minutes", True),
        ("2026-10-19T10:30:00-07:00", True),
        ("Where is the PO number?", False),
        ("ok", False),
    ],
)
def test_usable_eta_detection_is_conservative(answer: str, expected: bool) -> None:
    assert is_usable_eta(answer) is expected


@pytest.mark.asyncio
async def test_unrelated_answer_does_not_resolve_eta_request() -> None:
    pool = FakePool(
        fetchrow_result={
            "request_id": "request-1",
            "request_type": "eta_request",
            "state": "open",
        }
    )
    store = RequestStore(pool)  # type: ignore[arg-type]

    resolved = await store.resolve_answer(
        "broker-1", "load-1", "request-1", "What is the PO number?"
    )

    assert resolved is False
    assert len(pool.calls) == 1


@pytest.mark.asyncio
async def test_eta_answer_resolves_only_open_request() -> None:
    pool = FakePool(
        fetchrow_result={
            "request_id": "request-1",
            "request_type": "eta_request",
            "state": "open",
        }
    )
    store = RequestStore(pool)  # type: ignore[arg-type]

    resolved = await store.resolve_answer(
        "broker-1", "load-1", "request-1", "ETA 10:30 AM"
    )

    assert resolved is True
    assert len(pool.calls) == 2
    assert pool.calls[1][1][3] == RequestState.ANSWERED


@pytest.mark.asyncio
async def test_reopen_requires_relevance_and_sop_approval() -> None:
    pool = FakePool()
    store = RequestStore(pool)  # type: ignore[arg-type]
    request = OpenRequest(
        request_id="request-2",
        load_id="load-1",
        request_type=RequestType.ETA,
        question_text="What is your ETA?",
    )

    with pytest.raises(RequestTransitionError, match="still relevant"):
        await store.reopen(
            "broker-1",
            "request-1",
            request,
            remains_relevant=True,
            sop_requires_reopen=False,
        )

    assert pool.calls == []


@pytest.mark.asyncio
async def test_expiration_is_guarded_by_expiry_time() -> None:
    pool = FakePool()
    store = RequestStore(pool)  # type: ignore[arg-type]

    await store.transition(
        "broker-1", "load-1", "request-1", RequestState.EXPIRED
    )

    query, args = pool.calls[0]
    assert "expires_at <= now()" in query
    assert args[4] is True
