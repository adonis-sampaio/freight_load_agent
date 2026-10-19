"""Timer adapter interface and in-memory mock."""

from typing import Any, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from adapters.action_adapter import AdapterAcknowledgement, AdapterError


class TimerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    load_id: str = Field(min_length=1)
    timer_name: str = Field(min_length=1)
    trigger_at: AwareDatetime
    context: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class TimerAdapter(Protocol):
    async def schedule(self, request: TimerRequest) -> AdapterAcknowledgement: ...


class MockTimerAdapter:
    def __init__(self, failure: AdapterError | None = None) -> None:
        self.failure = failure
        self.scheduled: list[TimerRequest] = []

    async def schedule(
        self, request: TimerRequest
    ) -> AdapterAcknowledgement:
        if self.failure is not None:
            raise self.failure
        self.scheduled.append(request)
        return AdapterAcknowledgement(
            accepted=True,
            external_id=f"timer_{len(self.scheduled)}",
            status="scheduled",
        )
