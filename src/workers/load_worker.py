"""Claim queued load events and pass them through decision orchestration."""

import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol

from app.agent.decision_context import DecisionContext, NoApplicableSOPError
from app.agent.load_runner import LoadRunner
from models.contact import Contact
from models.event import LoadEvent
from models.load import Load

logger = logging.getLogger(__name__)


class EventTimeline(Protocol):
    async def claim_next_event(self) -> Mapping[str, Any] | None: ...

    async def finish_event(
        self,
        broker_id: str,
        load_id: str,
        event_id: str,
        *,
        failed: bool = False,
    ) -> None: ...


class LoadDataRepository(Protocol):
    async def get_load(self, broker_id: str, load_id: str) -> Load: ...

    async def get_contacts(
        self, broker_id: str, load_id: str
    ) -> list[Contact]: ...

    async def update_operational_state(self, load: Load) -> None: ...


class ContextBuilder(Protocol):
    async def build(
        self,
        broker_id: str,
        event: LoadEvent,
        load: Load,
        contacts: list[Contact],
    ) -> DecisionContext: ...


class DecisionProcessor(Protocol):
    async def process(self, context: DecisionContext) -> object: ...

    async def record_no_applicable_sop(
        self,
        broker_id: str,
        event: LoadEvent,
        load: Load,
    ) -> object: ...


class LoadWorker:
    def __init__(
        self,
        timeline: EventTimeline,
        loads: LoadDataRepository,
        context_builder: ContextBuilder,
        decision_engine: DecisionProcessor,
        load_runner: LoadRunner | None = None,
    ) -> None:
        self._timeline = timeline
        self._loads = loads
        self._context_builder = context_builder
        self._decision_engine = decision_engine
        self._load_runner = load_runner or LoadRunner(loads)

    async def run_once(self) -> bool:
        row = await self._timeline.claim_next_event()
        if row is None:
            return False

        values = dict(row)
        broker_id = values.pop("broker_id")
        values.pop("processing_status", None)
        values.pop("processing_started_at", None)
        payload = values["payload_json"]
        if isinstance(payload, str):
            values["payload_json"] = json.loads(payload)
        event = LoadEvent.model_validate(values)

        try:
            load = await self._loads.get_load(broker_id, event.load_id)
            load = await self._load_runner.apply_event(
                broker_id,
                event,
                load,
            )
            contacts = await self._loads.get_contacts(broker_id, event.load_id)
            try:
                context = await self._context_builder.build(
                    broker_id,
                    event,
                    load,
                    contacts,
                )
            except NoApplicableSOPError:
                await self._decision_engine.record_no_applicable_sop(
                    broker_id,
                    event,
                    load,
                )
            else:
                await self._decision_engine.process(context)
        except Exception:
            logger.exception(
                "load_event_processing_failed",
                extra={
                    "broker_id": broker_id,
                    "load_id": event.load_id,
                    "event_id": event.event_id,
                    "correlation_id": event.correlation_id,
                },
            )
            await self._timeline.finish_event(
                broker_id,
                event.load_id,
                event.event_id,
                failed=True,
            )
            raise

        await self._timeline.finish_event(
            broker_id,
            event.load_id,
            event.event_id,
        )
        logger.info(
            "load_event_processed",
            extra={
                "broker_id": broker_id,
                "load_id": event.load_id,
                "event_id": event.event_id,
                "correlation_id": event.correlation_id,
            },
        )
        return True
