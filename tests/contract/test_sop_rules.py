from datetime import UTC, datetime
import hashlib

import pytest

from app.agent.decision_context import SOPSectionNotFoundError
from app.rules.rule_selector import (
    AmbiguousSOPSelectionError,
    SOPRuleSelector,
)
from app.rules.sop_loader import SOPLoader
from models.common import EventType, LoadStage
from models.sop import SOPRule


def rule_row(
    rule_id: str,
    scope: str,
    *,
    client_id: str | None = None,
    load_type: str | None = None,
    priority: int = 0,
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "scope": scope,
        "client_id": client_id,
        "load_type": load_type,
        "priority": priority,
    }


def test_load_type_override_precedes_client_default_and_standard() -> None:
    selected = SOPRuleSelector.select(
        [
            rule_row("standard", "standard"),
            rule_row("client", "client_override", client_id="broker-1"),
            rule_row(
                "refrigerated",
                "load_type_override",
                client_id="broker-1",
                load_type="refrigerated",
            ),
        ],
        client_id="broker-1",
        load_type="refrigerated",
    )

    assert selected["rule_id"] == "refrigerated"


def test_global_load_type_override_precedes_client_default() -> None:
    selected = SOPRuleSelector.select(
        [
            rule_row("standard", "standard"),
            rule_row("client", "client_override", client_id="broker-1"),
            rule_row(
                "refrigerated",
                "load_type_override",
                load_type="refrigerated",
            ),
        ],
        client_id="broker-1",
        load_type="refrigerated",
    )

    assert selected["rule_id"] == "refrigerated"


def test_client_override_precedes_standard_fallback() -> None:
    selected = SOPRuleSelector.select(
        [
            rule_row("standard", "standard"),
            rule_row("client", "client_override", client_id="broker-1"),
        ],
        client_id="broker-1",
        load_type="dry",
    )

    assert selected["rule_id"] == "client"


def test_standard_is_selected_when_no_override_matches() -> None:
    selected = SOPRuleSelector.select(
        [
            rule_row("standard", "standard"),
            rule_row(
                "other-client",
                "client_override",
                client_id="broker-2",
            ),
            rule_row(
                "other-type",
                "load_type_override",
                load_type="refrigerated",
            ),
        ],
        client_id="broker-1",
        load_type="dry",
    )

    assert selected["rule_id"] == "standard"


def test_equal_precedence_and_priority_is_rejected() -> None:
    with pytest.raises(AmbiguousSOPSelectionError):
        SOPRuleSelector.select(
            [
                rule_row("client-a", "client_override", client_id="broker-1"),
                rule_row("client-b", "client_override", client_id="broker-1"),
            ],
            client_id="broker-1",
            load_type=None,
        )


def test_rule_scope_requires_client_or_load_type_metadata() -> None:
    with pytest.raises(ValueError, match="client_id"):
        SOPRule.model_validate(
            {
                "rule_id": "client-rule",
                "document_id": "client-doc",
                "scope": "client_override",
                "section_ref": "pickup.delay",
                "version": "v1",
                "effective_from": "2026-01-01T00:00:00Z",
            }
        )


def test_markdown_loader_versions_content_and_validates_section(tmp_path) -> None:
    markdown = "# Client SOP\n\n## pickup.delay\nCollect an ETA.\n"
    path = tmp_path / "client.md"
    path.write_text(markdown, encoding="utf-8")
    rule = SOPRule(
        rule_id="client-rule",
        document_id="client-doc",
        client_id="broker-1",
        scope="client_override",
        load_stage=LoadStage.AT_PICKUP,
        section_ref="pickup.delay",
        applicable_event_types=[EventType.PING],
        version="rule-v1",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    document, rules = SOPLoader().load_markdown(
        path,
        document_id="client-doc",
        client_id="broker-1",
        chapter_name="Client SOP",
        version="doc-v1",
        rules=[rule],
    )

    assert document.content_markdown == markdown
    assert document.content_hash == (
        f"sha256:{hashlib.sha256(markdown.encode()).hexdigest()}"
    )
    assert rules == (rule,)


def test_markdown_loader_rejects_missing_indexed_section(tmp_path) -> None:
    path = tmp_path / "client.md"
    path.write_text("# Client SOP\n", encoding="utf-8")
    rule = SOPRule(
        rule_id="client-rule",
        document_id="client-doc",
        client_id="broker-1",
        scope="client_override",
        section_ref="pickup.delay",
        applicable_event_types=[EventType.PING],
        version="rule-v1",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(SOPSectionNotFoundError):
        SOPLoader().load_markdown(
            path,
            document_id="client-doc",
            client_id="broker-1",
            chapter_name="Client SOP",
            version="doc-v1",
            rules=[rule],
        )
    with pytest.raises(ValueError, match="load_type"):
        SOPRule.model_validate(
            {
                "rule_id": "load-type-rule",
                "document_id": "load-type-doc",
                "scope": "load_type_override",
                "section_ref": "pickup.delay",
                "version": "v1",
                "effective_from": "2026-01-01T00:00:00Z",
            }
        )
