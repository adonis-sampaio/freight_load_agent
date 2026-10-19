"""SOP document and indexed-rule records."""

from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from .common import EventType, LoadStage, StrictModel


class SOPScope(StrEnum):
    STANDARD = "standard"
    CLIENT_OVERRIDE = "client_override"
    LOAD_TYPE_OVERRIDE = "load_type_override"


class SOPDocument(StrictModel):
    document_id: str = Field(min_length=1)
    client_id: str | None = None
    chapter_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_markdown: str
    content_hash: str = Field(min_length=1)


class SOPRule(StrictModel):
    rule_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    client_id: str | None = None
    load_type: str | None = None
    scope: SOPScope
    load_stage: LoadStage | None = None
    section_ref: str = Field(min_length=1)
    applicable_event_types: list[EventType] = Field(default_factory=list)
    priority: int = 0
    version: str = Field(min_length=1)
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_scope_and_effective_range(self) -> "SOPRule":
        if self.scope == SOPScope.STANDARD and self.client_id is not None:
            raise ValueError("standard SOP rules cannot specify client_id")
        if self.scope == SOPScope.CLIENT_OVERRIDE and self.client_id is None:
            raise ValueError("client overrides require client_id")
        if self.scope == SOPScope.LOAD_TYPE_OVERRIDE and self.load_type is None:
            raise ValueError("load-type overrides require load_type")
        if self.effective_to is not None and self.effective_from >= self.effective_to:
            raise ValueError("effective_to must be after effective_from")
        return self
