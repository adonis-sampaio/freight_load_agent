"""Email action adapter interface and in-memory mock."""

from typing import Protocol, runtime_checkable

from adapters.action_adapter import AdapterAcknowledgement, AdapterError


@runtime_checkable
class EmailAdapter(Protocol):
    async def send(
        self,
        thread_id: str,
        recipients: list[str],
        subject: str,
        body: str,
    ) -> AdapterAcknowledgement: ...


class MockEmailAdapter:
    def __init__(self, failure: AdapterError | None = None) -> None:
        self.failure = failure
        self.sent: list[tuple[str, list[str], str, str]] = []

    async def send(
        self,
        thread_id: str,
        recipients: list[str],
        subject: str,
        body: str,
    ) -> AdapterAcknowledgement:
        if not all((thread_id, recipients, subject.strip(), body.strip())):
            raise ValueError("email thread, recipients, subject, and body are required")
        if self.failure is not None:
            raise self.failure
        self.sent.append((thread_id, recipients, subject, body))
        return AdapterAcknowledgement(
            accepted=True,
            external_id=f"email_{len(self.sent)}",
            status="queued",
        )
