from datetime import UTC, datetime
from typing import Any

import pytest

from app.agent.decision_context import (
    AmbiguousSOPError,
    DecisionContextBuilder,
    SOPSectionNotFoundError,
    extract_markdown_section,
    _safe_payload,
)
from models.common import EventChannel, EventType
from models.contact import Contact
from models.event import LoadEvent
from models.load import Load


class FakeTimeline:
    def __init__(self, recent: list[dict[str, Any]]) -> None:
        self.recent = recent

    async def get_recent_events(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return self.recent


class FakeRequests:
    async def get_open_requests(self, *_args: Any) -> list[Any]:
        return []


class FakeSOPs:
    def __init__(self, matches: list[dict[str, Any]]) -> None:
        self.matches = matches

    async def find_applicable_sops(self, **_kwargs: Any) -> list[Any]:
        return self.matches


def standard_sop_row(priority: int = 1) -> dict[str, Any]:
    return {
        "rule_id": "rule-1",
        "document_id": "standard-1",
        "scope": "standard",
        "load_stage": "at_pickup",
        "section_ref": "pickup.delay",
        "applicable_event_types": ["ping"],
        "priority": priority,
        "version": "rule-v1",
        "effective_from": datetime(2026, 1, 1, tzinfo=UTC),
        "effective_to": None,
        "client_id": None,
        "chapter_name": "Standard",
        "document_version": "doc-v1",
        "content_markdown": (
            "# Standard\n## pickup.delay\nRequest an ETA for a delayed pickup.\n"
            "## delivery\nCollect POD.\n"
        ),
        "content_hash": "sha256:abc",
    }


@pytest.mark.asyncio
async def test_builder_sends_only_allowlisted_facts_and_safe_event_fields() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    recent_event = {
        "event_id": "previous-1",
        "broker_id": "broker-1",
        "load_id": "load-1",
        "event_type": "message",
        "source_channel": "sms",
        "source_contact_id": "driver-1",
        "payload_json": {"text": "What's the PO?", "personal_note": "private"},
        "processing_status": "completed",
        "occurred_at": now,
        "ingested_at": now,
        "correlation_id": "corr-1",
    }
    builder = DecisionContextBuilder(
        FakeTimeline([recent_event]),  # type: ignore[arg-type]
        FakeRequests(),  # type: ignore[arg-type]
        FakeSOPs([standard_sop_row()]),  # type: ignore[arg-type]
    )
    load = Load(
        load_id="load-1",
        broker_id="broker-1",
        broker_name="Broker",
        current_stage="at_pickup",
        available_facts={
            "po_number": "PO-123",
            "driver_ssn": "must-not-be-sent",
        },
    )
    event = LoadEvent(
        event_id="event-1",
        load_id="load-1",
        event_type=EventType.PING,
        source_channel=EventChannel.SYSTEM,
        payload_json={
            "latitude": 47.6,
            "longitude": -122.3,
            "device_token": "must-not-be-sent",
        },
        occurred_at=now,
    )
    contact = Contact(
        contact_id="driver-1",
        load_id="load-1",
        role="driver",
        channels=["sms"],
        external_ref="+15551234567",
        primary_channels={"eta": "sms"},
    )

    context = await builder.build("broker-1", event, load, [contact])

    assert context.llm_request.load_context.available_facts == {
        "po_number": "PO-123",
        "delay_risk": "low",
    }
    assert context.llm_request.event.payload == {
        "latitude": 47.6,
        "longitude": -122.3,
    }
    assert context.llm_request.load_context.recent_events[0].payload == {
        "text": "What's the PO?"
    }
    assert context.llm_request.active_sop.sections[0].text == (
        "Request an ETA for a delayed pickup."
    )


def test_context_rejects_equal_priority_standard_rules() -> None:
    with pytest.raises(AmbiguousSOPError, match="same priority"):
        DecisionContextBuilder._select_standard_sop(
            [standard_sop_row(), {**standard_sop_row(), "rule_id": "rule-2"}]
        )


def test_markdown_section_extraction_requires_exact_heading() -> None:
    with pytest.raises(SOPSectionNotFoundError, match="was not found"):
        extract_markdown_section("## pickup\nRequest ETA.", "pickup.delay")


def test_metadata_update_context_excludes_unallowlisted_fields() -> None:
    assert _safe_payload(
        EventType.METADATA_UPDATE,
        {"field": "driver_ssn", "value": "must-not-be-sent"},
        frozenset({"po_number"}),
    ) == {}
