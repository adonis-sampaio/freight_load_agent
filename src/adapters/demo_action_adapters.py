"""Non-delivering action adapters for the deterministic worker demonstration."""

import logging
from typing import Any
from uuid import uuid4

from adapters.action_adapter import AdapterAcknowledgement
from adapters.timer_adapter import TimerRequest

logger = logging.getLogger(__name__)


def _acknowledgement(action: str, identifier: str) -> AdapterAcknowledgement:
    logger.info(
        "demo_action_simulated",
        extra={"action": action, "demo_id": identifier},
    )
    return AdapterAcknowledgement(
        accepted=True,
        external_id=f"demo_{identifier}_{uuid4().hex}",
        status="simulated",
    )


class DemoSMSAdapter:
    async def send(self, destination: str, message: str) -> AdapterAcknowledgement:
        if not destination or not message.strip():
            raise ValueError("SMS destination and message are required")
        return _acknowledgement("sms_send", "sms")


class DemoEmailAdapter:
    async def send(
        self,
        thread_id: str,
        recipients: list[str],
        subject: str,
        body: str,
    ) -> AdapterAcknowledgement:
        if not all((thread_id, recipients, subject.strip(), body.strip())):
            raise ValueError("email thread, recipients, subject, and body are required")
        return _acknowledgement("email_send", "email")


class DemoTaskAdapter:
    async def create(
        self,
        load_id: str,
        priority: str,
        task_type: str,
        summary: str,
    ) -> AdapterAcknowledgement:
        if not all((load_id, priority, task_type, summary.strip())):
            raise ValueError("task load, priority, type, and summary are required")
        return _acknowledgement("hero_task", "task")


class DemoTimerAdapter:
    async def schedule(self, request: TimerRequest) -> AdapterAcknowledgement:
        return _acknowledgement("timer_set", request.timer_name)


class DemoGenericActionAdapter:
    async def execute(
        self, load_id: str, payload: dict[str, Any]
    ) -> AdapterAcknowledgement:
        if not load_id:
            raise ValueError("load_id is required")
        return _acknowledgement("generic_action", "action")
