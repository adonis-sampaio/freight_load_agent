"""Load and contact queries used by event processing."""

import json
from typing import Any

import asyncpg

from models.contact import Contact
from models.common import DelayRisk
from models.load import Load, Stop


class LoadRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_load(self, broker_id: str, load_id: str) -> Load:
        row = await self._pool.fetchrow(
            """
            SELECT load_id, broker_id, broker_name, shipper_name,
                   carrier_name, carrier_mc_number, commodity, load_type,
                   current_stage, delay_risk, status, available_facts_json,
                   created_at, updated_at
            FROM loads
            WHERE broker_id = $1 AND load_id = $2
            """,
            broker_id,
            load_id,
        )
        if row is None:
            raise LookupError(f"load {load_id} does not exist for broker")
        values = dict(row)
        values["available_facts"] = _json_value(
            values.pop("available_facts_json")
        )
        stop_rows = await self._pool.fetch(
            """
            SELECT stop_id, sequence, type, name, address, timezone,
                   appointment_type, appointment_start, appointment_end,
                   appointment_time
            FROM stops
            WHERE broker_id = $1 AND load_id = $2
            ORDER BY sequence
            """,
            broker_id,
            load_id,
        )
        values["stops"] = [Stop.model_validate(dict(stop)) for stop in stop_rows]
        return Load.model_validate(values)

    async def get_contacts(self, broker_id: str, load_id: str) -> list[Contact]:
        rows = await self._pool.fetch(
            """
            SELECT contact_id, load_id, role, channels, external_ref,
                   primary_channels, is_active
            FROM contacts
            WHERE broker_id = $1 AND load_id = $2 AND is_active = TRUE
            ORDER BY contact_id
            """,
            broker_id,
            load_id,
        )
        contacts = []
        for row in rows:
            values = dict(row)
            values["channels"] = _json_value(values["channels"])
            values["primary_channels"] = _json_value(
                values["primary_channels"]
            )
            contacts.append(Contact.model_validate(values))
        return contacts

    async def update_operational_state(self, load: Load) -> None:
        await self._pool.execute(
            """
            UPDATE loads
            SET current_stage = $3, delay_risk = $4,
                available_facts_json = $5::jsonb, updated_at = now()
            WHERE broker_id = $1 AND load_id = $2
            """,
            load.broker_id,
            load.load_id,
            load.current_stage,
            load.delay_risk,
            json.dumps(load.available_facts),
        )


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value
