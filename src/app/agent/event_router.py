"""Durable event-ingestion endpoint."""

from uuid import uuid4

from fastapi import APIRouter, Request

from app.memory.load_timeline import LoadTimelineRepository
from models.event import EventIngestRequest, EventIngestResponse, LoadEvent

router = APIRouter()


@router.post(
    "/v1/events/ingest",
    response_model=EventIngestResponse,
    status_code=202,
)
async def ingest_event(
    body: EventIngestRequest, request: Request
) -> EventIngestResponse:
    database = request.app.state.database
    event_id = f"evt_{uuid4().hex}"
    event = LoadEvent(
        event_id=event_id,
        load_id=body.load_id,
        event_type=body.event_type,
        source_channel=body.source_channel,
        source_contact_id=body.source_contact_id,
        payload_json=body.payload,
        occurred_at=body.occurred_at,
        correlation_id=body.correlation_id or f"corr_{uuid4().hex}",
    )
    await LoadTimelineRepository(database.pool).append_event(body.broker_id, event)
    return EventIngestResponse(event_id=event_id)
