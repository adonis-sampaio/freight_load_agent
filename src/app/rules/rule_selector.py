"""Deterministic selection of active SOP rules before model interpretation."""

from collections.abc import Mapping, Sequence
from typing import Any


class AmbiguousSOPSelectionError(LookupError):
    """Raised when equally preferred active SOP rules match."""


class SOPRuleSelector:
    @staticmethod
    def select(
        rows: Sequence[Mapping[str, Any]],
        *,
        client_id: str,
        load_type: str | None,
    ) -> Mapping[str, Any] | None:
        matching: list[tuple[int, int, Mapping[str, Any]]] = []
        for row in rows:
            scope = row["scope"]
            scope_value = getattr(scope, "value", scope)
            rule_client = row.get("client_id")
            rule_load_type = row.get("load_type")

            if scope_value == "standard":
                rank = 1
            elif scope_value == "client_override":
                if rule_client != client_id:
                    continue
                if rule_load_type is None:
                    rank = 2
                elif rule_load_type == load_type:
                    rank = 4
                else:
                    continue
            elif scope_value == "load_type_override":
                if rule_load_type != load_type:
                    continue
                if rule_client not in (None, client_id):
                    continue
                rank = 4 if rule_client == client_id else 3
            else:
                continue

            matching.append((rank, int(row.get("priority", 0)), row))

        if not matching:
            return None
        highest = max((rank, priority) for rank, priority, _ in matching)
        best = [
            row
            for rank, priority, row in matching
            if (rank, priority) == highest
        ]
        if len(best) > 1:
            raise AmbiguousSOPSelectionError(
                "multiple active SOP rules match at the same priority and precedence"
            )
        return best[0]
