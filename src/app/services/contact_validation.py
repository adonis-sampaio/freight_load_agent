"""Shared destination and operational-channel validation."""

import re
from collections.abc import Iterable

from models.action import ActionType
from models.contact import Contact, ContactChannel
from models.decision import ProposedAction

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


class InvalidContactError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def resolve_action_contact(
    action: ProposedAction,
    contacts: Iterable[Contact],
    *,
    load_id: str,
    expected_channel: ContactChannel,
) -> Contact:
    contact_id = action.contact_id
    contact_map = {contact.contact_id: contact for contact in contacts}
    if contact_id is None or contact_id not in contact_map:
        raise InvalidContactError("contact_not_available")
    contact = contact_map[contact_id]
    if not contact.is_active or contact.load_id != load_id:
        raise InvalidContactError("contact_not_available")
    if expected_channel not in contact.channels:
        raise InvalidContactError("contact_channel_unavailable")

    payload = action.payload
    operational_step = payload.get("operational_step")
    if operational_step is None:
        operational_step = (
            "eta"
            if payload.get("request_type") == "eta_request"
            else "escalation"
            if expected_channel == ContactChannel.EMAIL
            else "default"
        )
    if contact.primary_channels.get(str(operational_step)) != expected_channel:
        raise InvalidContactError("contact_primary_channel_mismatch")

    if expected_channel == ContactChannel.SMS:
        if action.action_type != ActionType.SMS_SEND or not _PHONE_PATTERN.fullmatch(
            contact.external_ref
        ):
            raise InvalidContactError("invalid_contact_destination")
    elif expected_channel == ContactChannel.EMAIL:
        if action.action_type != ActionType.EMAIL_SEND or not _EMAIL_PATTERN.fullmatch(
            contact.external_ref
        ):
            raise InvalidContactError("invalid_contact_destination")
        if payload.get("to") != [contact.external_ref]:
            raise InvalidContactError("email_recipient_mismatch")
    return contact
