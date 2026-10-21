"""Compose the safe, deterministic demonstration worker."""

import logging

import asyncpg
from adapters.demo_action_adapters import (
    DemoEmailAdapter,
    DemoGenericActionAdapter,
    DemoSMSAdapter,
    DemoTaskAdapter,
    DemoTimerAdapter,
)
from app.agent.decision_context import DecisionContext, DecisionContextBuilder
from app.agent.decision_engine import DecisionEngine
from app.memory.action_ledger import ActionLedger
from app.memory.decision_store import DecisionStore
from app.memory.load_repository import LoadRepository
from app.memory.load_timeline import LoadTimelineRepository
from app.memory.request_store import RequestStore
from app.memory.sop_repository import SOPRepository
from app.rules.decision_validator import DecisionValidator
from app.services.action_executor import ActionExecutor
from config.settings import Settings
from models.action import ActionType
from models.decision import (
    DecisionRecord,
    LLMDecisionProposal,
    LLMDecisionRequest,
    ModelDisposition,
    UncertaintyLevel,
)
from workers.load_worker import LoadWorker

logger = logging.getLogger(__name__)


class DemoNoActionInterpreter:
    """Return a fixed no-action result; this class never accesses a provider."""

    async def interpret(self, request: LLMDecisionRequest) -> LLMDecisionProposal:
        section = request.active_sop.sections[0]
        return LLMDecisionProposal(
            schema_version="1",
            model_disposition=ModelDisposition.NO_ACTION,
            load_id=request.load_context.load_id,
            sop_document_id=request.active_sop.document_id,
            sop_version=request.active_sop.version,
            sop_content_hash=request.active_sop.content_hash,
            sop_section_ref=section.section_ref,
            supporting_passage=section.text,
            reason_code="demo_no_action",
            uncertainty=UncertaintyLevel.LOW,
        )


class DemoReviewRouter:
    """Expose review-required outcomes in logs, not a production escalation."""

    async def route(
        self, decision: DecisionRecord, context: DecisionContext
    ) -> None:
        logger.warning(
            "demo_review_required_not_routed",
            extra={
                "demo_only": True,
                "broker_id": context.broker_id,
                "load_id": context.load.load_id,
                "event_id": context.event.event_id,
                "decision_id": decision.decision_id,
            },
        )


def build_load_worker(
    pool: asyncpg.Pool,
    settings: Settings,
) -> tuple[LoadWorker, LoadTimelineRepository]:
    timeline = LoadTimelineRepository(pool)
    loads = LoadRepository(pool)
    requests = RequestStore(pool)
    sops = SOPRepository(pool)
    context_builder = DecisionContextBuilder(timeline, requests, sops)
    action_executor = ActionExecutor(
        ActionLedger(pool),
        sms=DemoSMSAdapter(),
        email=DemoEmailAdapter(),
        task=DemoTaskAdapter(),
        timer=DemoTimerAdapter(),
        additional_adapters={
            ActionType.SLACK_ALERT: DemoGenericActionAdapter(),
            ActionType.TMS_UPDATE: DemoGenericActionAdapter(),
            ActionType.STATUS_UPDATE: DemoGenericActionAdapter(),
        },
    )
    engine = DecisionEngine(
        interpreter=DemoNoActionInterpreter(),
        validator=DecisionValidator(),
        decision_store=DecisionStore(pool),
        request_store=requests,
        action_executor=action_executor,
        settings=settings,
        review_router=DemoReviewRouter(),
        demo_mode=True,
    )
    return (
        LoadWorker(timeline, loads, context_builder, engine),
        timeline,
    )
