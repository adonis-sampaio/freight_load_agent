from datetime import UTC, datetime

import pytest

from adapters.action_adapter import AdapterUnavailableError
from adapters.email_adapter import MockEmailAdapter
from adapters.sms_adapter import MockSMSAdapter
from adapters.task_adapter import MockTaskAdapter
from adapters.timer_adapter import MockTimerAdapter, TimerRequest


@pytest.mark.asyncio
async def test_sms_adapter_returns_machine_readable_acknowledgement() -> None:
    adapter = MockSMSAdapter()

    result = await adapter.send("+15551234567", "What is your ETA?")

    assert result.accepted is True
    assert result.external_id == "sms_1"
    assert adapter.sent == [("+15551234567", "What is your ETA?")]


@pytest.mark.asyncio
async def test_adapter_failure_is_explicit() -> None:
    adapter = MockSMSAdapter(AdapterUnavailableError("SMS service unavailable"))

    with pytest.raises(AdapterUnavailableError, match="unavailable"):
        await adapter.send("+15551234567", "What is your ETA?")


@pytest.mark.asyncio
async def test_email_and_task_adapters_capture_contract_fields() -> None:
    email = MockEmailAdapter()
    task = MockTaskAdapter()

    email_result = await email.send(
        "thread-1",
        ["dispatcher@example.com"],
        "ETA required",
        "Please provide an ETA.",
    )
    task_result = await task.create(
        "load-1",
        "urgent",
        "driver_callback",
        "Call the driver for an ETA.",
    )

    assert email_result.status == "queued"
    assert task_result.status == "created"
    assert email.sent[0][0] == "thread-1"
    assert task.created[0][2] == "driver_callback"


@pytest.mark.asyncio
async def test_timer_adapter_requires_aware_trigger_time() -> None:
    adapter = MockTimerAdapter()
    request = TimerRequest(
        load_id="load-1",
        timer_name="eta_followup_1",
        trigger_at=datetime(2026, 10, 19, 10, 30, tzinfo=UTC),
        context={"request_id": "request-1"},
    )

    result = await adapter.schedule(request)

    assert result.status == "scheduled"
    assert adapter.scheduled == [request]
