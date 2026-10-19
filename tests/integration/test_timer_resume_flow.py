from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agent.decision_context import DecisionContextBuilder
from models.common import EventChannel, EventType
from models.event import LoadEvent, OpenRequest, RequestType
from tests.integration.agent_support import make_context


class Timeline:
    def __init__(self, origin: LoadEvent) -> None:
        self.origin = origin
        self.requested_correlations: list[str] = []

    async def get_recent_events(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def get_event_by_correlation_id(
        self, _broker_id: str, _load_id: str, correlation_id: str
    ) -> dict[str, Any] | None:
        self.requested_correlations.append(correlation_id)
        if correlation_id != self.origin.correlation_id:
            return None
        return {
            "event_id": self.origin.event_id,
            "broker_id": "broker-1",
            "load_id": self.origin.load_id,
            "event_type": self.origin.event_type,
            "source_channel": self.origin.source_channel,
            "source_contact_id": self.origin.source_contact_id,
            "payload_json": self.origin.payload_json,
            "processing_status": "completed",
            "occurred_at": self.origin.occurred_at,
            "ingested_at": self.origin.ingested_at,
            "correlation_id": self.origin.correlation_id,
        }


class Requests:
    def __init__(self, request: OpenRequest) -> None:
        self.request = request

    async def get_open_requests(self, *_args: Any) -> list[Any]:
        return [self.request.model_dump()]


class SOPs:
    async def find_applicable_sops(self, **_kwargs: Any) -> list[Any]:
        return [
            {
                "rule_id": "rule-1",
                "document_id": "standard-1",
                "scope": "standard",
                "client_id": None,
                "load_type": None,
                "load_stage": "at_pickup",
                "section_ref": "pickup.delay",
                "applicable_event_types": ["timer"],
                "priority": 1,
                "version": "rule-v1",
                "effective_from": datetime(2020, 1, 1, tzinfo=UTC),
                "effective_to": None,
                "document_client_id": None,
                "chapter_name": "Standard",
                "document_version": "doc-v1",
                "content_markdown": (
                    "# Standard\n## pickup.delay\nRequest an ETA.\n"
                ),
                "content_hash": "sha256:standard",
            }
        ]


@pytest.mark.asyncio
async def test_timer_context_reloads_originating_event_and_open_request() -> None:
    now = datetime.now(UTC)
    origin = LoadEvent(
        event_id="origin-message",
        load_id="load-1",
        event_type=EventType.MESSAGE,
        source_channel=EventChannel.SMS,
        source_contact_id="driver-1",
        payload_json={"text": "I will be late."},
        occurred_at=now - timedelta(minutes=45),
        correlation_id="request-correlation",
    )
    request = OpenRequest(
        request_id="request-1",
        load_id="load-1",
        request_type=RequestType.ETA,
        question_text="What is your ETA?",
        expires_at=now - timedelta(minutes=1),
        correlation_id=origin.correlation_id,
    )
    timer_context = make_context(
        "timer-1",
        EventType.TIMER,
        payload={"timer_id": "timer-1", "request_id": request.request_id},
        occurred_at=now,
        requests=[request],
    )
    timeline = Timeline(origin)
    builder = DecisionContextBuilder(
        timeline,  # type: ignore[arg-type]
        Requests(request),  # type: ignore[arg-type]
        SOPs(),  # type: ignore[arg-type]
    )

    context = await builder.build(
        "broker-1",
        timer_context.event,
        timer_context.load,
        list(timer_context.contacts),
    )

    assert timeline.requested_correlations == ["request-correlation"]
    assert context.open_requests[0].request_id == "request-1"
    assert context.llm_request.load_context.open_requests[0].request_id == "request-1"
    assert context.llm_request.load_context.recent_events[0].event_id == (
        "origin-message"
    )
