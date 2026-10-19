"""SMS action adapter interface and in-memory mock."""

from typing import Protocol, runtime_checkable

from adapters.action_adapter import AdapterAcknowledgement, AdapterError


@runtime_checkable
class SMSAdapter(Protocol):
    async def send(
        self, destination: str, message: str
    ) -> AdapterAcknowledgement: ...


class MockSMSAdapter:
    def __init__(self, failure: AdapterError | None = None) -> None:
        self.failure = failure
        self.sent: list[tuple[str, str]] = []

    async def send(
        self, destination: str, message: str
    ) -> AdapterAcknowledgement:
        if not destination or not message.strip():
            raise ValueError("SMS destination and message are required")
        if self.failure is not None:
            raise self.failure
        self.sent.append((destination, message))
        return AdapterAcknowledgement(
            accepted=True,
            external_id=f"sms_{len(self.sent)}",
            status="sent",
        )
