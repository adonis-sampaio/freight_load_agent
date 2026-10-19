from typing import Any

from fastapi.testclient import TestClient

from app.api import create_app


class FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        return "INSERT 0 1"


class FakeDatabase:
    def __init__(self) -> None:
        self.pool = FakePool()
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True


def test_ingest_persists_event_and_returns_queued_acknowledgement() -> None:
    database = FakeDatabase()
    app = create_app(database)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            "/v1/events/ingest",
            json={
                "broker_id": "broker-a",
                "load_id": "load-123",
                "event_type": "message",
                "source_channel": "sms",
                "source_contact_id": "driver-1",
                "payload": {"text": "What is the PO number?"},
                "occurred_at": "2026-10-13T07:12:00-07:00",
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["status"] == "queued_for_processing"
    assert body["event_id"].startswith("evt_")
    assert database.opened is True
    assert database.closed is True
    query, args = database.pool.calls[0]
    assert "'queued'" in query
    assert args[1] == "broker-a"
    assert args[2] == "load-123"
    assert args[-1].startswith("corr_")


def test_ingest_requires_broker_id_to_disambiguate_load() -> None:
    database = FakeDatabase()
    app = create_app(database)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            "/v1/events/ingest",
            json={
                "load_id": "load-123",
                "event_type": "message",
                "source_channel": "sms",
                "payload": {"text": "Where are you?"},
                "occurred_at": "2026-10-13T07:12:00-07:00",
            },
        )

    assert response.status_code == 422
    assert database.pool.calls == []
