"""Hero task adapter interface and in-memory mock."""

from typing import Protocol, runtime_checkable

from adapters.action_adapter import AdapterAcknowledgement, AdapterError


@runtime_checkable
class TaskAdapter(Protocol):
    async def create(
        self,
        load_id: str,
        priority: str,
        task_type: str,
        summary: str,
    ) -> AdapterAcknowledgement: ...


class MockTaskAdapter:
    def __init__(self, failure: AdapterError | None = None) -> None:
        self.failure = failure
        self.created: list[tuple[str, str, str, str]] = []

    async def create(
        self,
        load_id: str,
        priority: str,
        task_type: str,
        summary: str,
    ) -> AdapterAcknowledgement:
        if not all((load_id, priority, task_type, summary.strip())):
            raise ValueError("task load, priority, type, and summary are required")
        if self.failure is not None:
            raise self.failure
        self.created.append((load_id, priority, task_type, summary))
        return AdapterAcknowledgement(
            accepted=True,
            external_id=f"task_{len(self.created)}",
            status="created",
        )
