"""Build minimal, evidence-backed context for SOP interpretation."""

import json
import re
from typing import Any

import asyncpg
from pydantic import Field, model_validator

from app.memory.load_timeline import LoadTimelineRepository
from app.memory.request_store import RequestStore
from app.memory.sop_repository import SOPRepository
from app.rules.rule_selector import (
    AmbiguousSOPSelectionError,
    SOPRuleSelector,
)
from models.action import ActionType
from models.common import EventType
from models.contact import Contact
from models.common import StrictModel
from models.decision import (
    ActiveSOP,
    DecisionEvent,
    DecisionLoadContext,
    DecisionOpenRequest,
    LLMDecisionRequest,
    SOPSection,
)
from models.event import LoadEvent, OpenRequest
from models.load import Load
from models.sop import SOPDocument, SOPRule


class NoApplicableSOPError(LookupError):
    """No active SOP rule matches the event and load stage."""


class AmbiguousSOPError(LookupError):
    """More than one equally specific SOP rule matches."""


class SOPSectionNotFoundError(LookupError):
    """The SOP rule references a section absent from its document."""


class DecisionContext(StrictModel):
    broker_id: str
    event: LoadEvent
    load: Load
    active_rule: SOPRule
    active_document: SOPDocument
    open_requests: tuple[OpenRequest, ...] = ()
    contacts: tuple[Contact, ...] = ()
    llm_request: LLMDecisionRequest

    @model_validator(mode="after")
    def validate_load_scope(self) -> "DecisionContext":
        if self.event.load_id != self.load.load_id:
            raise ValueError("event and load identifiers must match")
        if self.active_rule.document_id != self.active_document.document_id:
            raise ValueError("selected rule and SOP document must match")
        return self


class DecisionContextBuilder:
    """Resolve the MVP standard SOP and minimize data sent to the LLM."""

    def __init__(
        self,
        timeline: LoadTimelineRepository,
        requests: RequestStore,
        sops: SOPRepository,
        *,
        fact_allowlist: frozenset[str] = frozenset(
            {
                "po_number",
                "pickup_appointment",
                "delivery_appointment",
                "trailer_temperature",
                "seal_number",
                "estimated_arrival",
            }
        ),
    ) -> None:
        self._timeline = timeline
        self._requests = requests
        self._sops = sops
        self._fact_allowlist = fact_allowlist

    async def build(
        self,
        broker_id: str,
        event: LoadEvent,
        load: Load,
        contacts: list[Contact],
    ) -> DecisionContext:
        if event.load_id != load.load_id:
            raise ValueError("event does not belong to the supplied load")

        recent_rows, request_rows, sop_rows = await self._load_inputs(
            broker_id, event, load
        )
        try:
            selected_sop = SOPRuleSelector.select(
                sop_rows,
                client_id=broker_id,
                load_type=load.load_type,
            )
        except AmbiguousSOPSelectionError as error:
            raise AmbiguousSOPError(str(error)) from error
        if selected_sop is None:
            raise NoApplicableSOPError(
                "no active SOP matches this client, load type, stage, and event"
            )
        active_rule, active_document = self._sop_from_row(selected_sop)
        section_text = extract_markdown_section(
            active_document.content_markdown, active_rule.section_ref
        )
        current_event = _decision_event(event, self._fact_allowlist)
        recent_events = [
            _decision_event(_load_event_from_row(row), self._fact_allowlist)
            for row in recent_rows
        ]
        open_requests = tuple(_open_request_from_row(row) for row in request_rows)
        allowed_facts = {
            key: value
            for key, value in load.available_facts.items()
            if key in self._fact_allowlist
        }
        allowed_facts.update(
            {
                "delay_risk": load.delay_risk,
            }
        )
        llm_request = LLMDecisionRequest(
            event=current_event,
            load_context=DecisionLoadContext(
                load_id=load.load_id,
                current_stage=load.current_stage,
                available_facts=allowed_facts,
                open_requests=[
                    DecisionOpenRequest(
                        request_id=request.request_id,
                        request_type=request.request_type,
                        state="open",
                    )
                    for request in open_requests
                ],
                recent_events=recent_events,
            ),
            active_sop=ActiveSOP(
                document_id=active_document.document_id,
                version=active_document.version,
                content_hash=active_document.content_hash,
                sections=[
                    SOPSection(
                        section_ref=active_rule.section_ref,
                        text=section_text,
                    )
                ],
            ),
            allowed_action_types=list(ActionType),
        )
        return DecisionContext(
            broker_id=broker_id,
            event=event,
            load=load,
            active_rule=active_rule,
            active_document=active_document,
            open_requests=open_requests,
            contacts=tuple(contacts),
            llm_request=llm_request,
        )

    async def _load_inputs(
        self, broker_id: str, event: LoadEvent, load: Load
    ) -> tuple[list[Any], list[Any], list[Any]]:
        recent = await self._timeline.get_recent_events(
            broker_id,
            load.load_id,
            before=event.occurred_at,
            limit=5,
        )
        open_requests = await self._requests.get_open_requests(
            broker_id, load.load_id
        )
        if event.event_type == EventType.TIMER:
            request_id = event.payload_json.get("request_id")
            timer_request = next(
                (
                    request
                    for request in open_requests
                    if request["request_id"] == request_id
                    and request["state"] == "open"
                ),
                None,
            )
            if timer_request is not None:
                correlation_id = timer_request.get("correlation_id")
                if isinstance(correlation_id, str):
                    origin = await self._timeline.get_event_by_correlation_id(
                        broker_id,
                        load.load_id,
                        correlation_id,
                    )
                    if origin is not None and not any(
                        row["event_id"] == origin["event_id"] for row in recent
                    ):
                        recent = [origin, *recent[:4]]
                        recent.sort(
                            key=lambda row: (
                                row["occurred_at"],
                                row["ingested_at"],
                            ),
                            reverse=True,
                        )
        sop_rules = await self._sops.find_applicable_sops(
            client_id=broker_id,
            load_type=load.load_type,
            stage=load.current_stage,
            event_type=event.event_type,
            at=event.occurred_at,
        )
        return recent, open_requests, sop_rules

    @staticmethod
    def _select_standard_sop(
        rows: list[Any],
    ) -> tuple[SOPRule, SOPDocument]:
        try:
            selected = SOPRuleSelector.select(
                rows,
                client_id="",
                load_type=None,
            )
        except AmbiguousSOPSelectionError as error:
            raise AmbiguousSOPError(str(error)) from error
        if selected is None:
            raise NoApplicableSOPError("no active standard SOP matches this event")
        return DecisionContextBuilder._sop_from_row(selected)

    @staticmethod
    def _sop_from_row(row: Any) -> tuple[SOPRule, SOPDocument]:
        event_types = row["applicable_event_types"]
        if isinstance(event_types, str):
            event_types = json.loads(event_types)
        rule = SOPRule(
            rule_id=row["rule_id"],
            document_id=row["document_id"],
            client_id=row.get("client_id"),
            load_type=row.get("load_type"),
            scope=row["scope"],
            load_stage=row["load_stage"],
            section_ref=row["section_ref"],
            applicable_event_types=event_types,
            priority=row["priority"],
            version=row["version"],
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
        )
        document = SOPDocument(
            document_id=row["document_id"],
            client_id=row.get(
                "document_client_id",
                row.get("client_id"),
            ),
            chapter_name=row["chapter_name"],
            version=row["document_version"],
            content_markdown=row["content_markdown"],
            content_hash=row["content_hash"],
        )
        return rule, document


def extract_markdown_section(markdown: str, section_ref: str) -> str:
    """Extract an exact heading section, stopping at the next peer heading."""
    heading_pattern = re.compile(
        r"^(#{1,6})\s+(.+?)\s*$",
        re.MULTILINE,
    )
    headings = list(heading_pattern.finditer(markdown))
    for index, heading in enumerate(headings):
        if heading.group(2).strip() != section_ref:
            continue
        start = heading.end()
        end = len(markdown)
        level = len(heading.group(1))
        for following in headings[index + 1 :]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        passage = markdown[start:end].strip()
        if not passage:
            raise SOPSectionNotFoundError(
                f"SOP section {section_ref!r} is empty"
            )
        return passage
    raise SOPSectionNotFoundError(
        f"SOP section {section_ref!r} was not found"
    )


def _decision_event(
    event: LoadEvent, fact_allowlist: frozenset[str]
) -> DecisionEvent:
    return DecisionEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        source_channel=event.source_channel,
        source_contact_id=event.source_contact_id,
        payload=_safe_payload(event.event_type, event.payload_json, fact_allowlist),
        occurred_at=event.occurred_at,
    )


def _safe_payload(
    event_type: EventType,
    payload: dict[str, Any],
    fact_allowlist: frozenset[str],
) -> dict[str, Any]:
    if event_type == EventType.METADATA_UPDATE:
        field = payload.get("field")
        if field not in fact_allowlist or "value" not in payload:
            return {}
        return {"field": field, "value": payload["value"]}
    fields_by_type: dict[EventType, tuple[str, ...]] = {
        EventType.MESSAGE: ("text", "subject"),
        EventType.PING: (
            "latitude",
            "longitude",
            "location_name",
            "estimated_arrival",
            "stop_id",
            "delay_risk",
        ),
        EventType.TIMER: ("timer_id", "request_id", "scheduled_action_id"),
        EventType.ATTACHMENT: ("document_id", "document_type"),
        EventType.METADATA_UPDATE: (),
        EventType.STAGE_CHANGE: ("current_stage",),
    }
    return {
        key: payload[key]
        for key in fields_by_type[event_type]
        if key in payload
    }


def _load_event_from_row(row: asyncpg.Record) -> LoadEvent:
    values = dict(row)
    raw_payload = values["payload_json"]
    if isinstance(raw_payload, str):
        values["payload_json"] = json.loads(raw_payload)
    values.pop("processing_status", None)
    values.pop("broker_id", None)
    return LoadEvent.model_validate(values)


def _open_request_from_row(row: asyncpg.Record) -> OpenRequest:
    values = dict(row)
    values.pop("broker_id", None)
    return OpenRequest.model_validate(values)
