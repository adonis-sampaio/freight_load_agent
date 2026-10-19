"""Contact records and operational communication channels."""

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .common import StrictModel


class ContactRole(StrEnum):
    DRIVER = "driver"
    DISPATCHER = "dispatcher"
    BROKER = "broker"
    HERO = "hero"
    CARRIER = "carrier"


class ContactChannel(StrEnum):
    SMS = "sms"
    EMAIL = "email"
    SLACK = "slack"


class Contact(StrictModel):
    contact_id: str = Field(min_length=1)
    load_id: str = Field(min_length=1)
    role: ContactRole
    channels: list[ContactChannel] = Field(min_length=1)
    external_ref: str = Field(min_length=1)
    primary_channels: dict[str, ContactChannel] = Field(default_factory=dict)
    is_active: bool = True

    @field_validator("channels")
    @classmethod
    def unique_channels(cls, value: list[ContactChannel]) -> list[ContactChannel]:
        if len(value) != len(set(value)):
            raise ValueError("contact channels must be unique")
        return value

    @model_validator(mode="after")
    def validate_primary_channels(self) -> "Contact":
        unavailable = set(self.primary_channels.values()) - set(self.channels)
        if unavailable:
            raise ValueError("each primary channel must be present in channels")
        if any(not step.strip() for step in self.primary_channels):
            raise ValueError("primary channel step names must not be empty")
        return self
