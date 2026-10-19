"""Load immutable Markdown SOP versions and validate their indexed rules."""

import hashlib
from pathlib import Path
from collections.abc import Sequence

from app.agent.decision_context import extract_markdown_section
from models.sop import SOPDocument, SOPRule


class SOPLoader:
    def load_markdown(
        self,
        path: Path,
        *,
        document_id: str,
        client_id: str | None,
        chapter_name: str,
        version: str,
        rules: Sequence[SOPRule],
    ) -> tuple[SOPDocument, tuple[SOPRule, ...]]:
        markdown = path.read_text(encoding="utf-8")
        document = SOPDocument(
            document_id=document_id,
            client_id=client_id,
            chapter_name=chapter_name,
            version=version,
            content_markdown=markdown,
            content_hash=f"sha256:{hashlib.sha256(markdown.encode()).hexdigest()}",
        )
        validated_rules = tuple(rules)
        rule_ids: set[str] = set()
        for rule in validated_rules:
            if rule.document_id != document_id:
                raise ValueError("every SOP rule must reference the loaded document")
            if rule.rule_id in rule_ids:
                raise ValueError("SOP rule identifiers must be unique")
            rule_ids.add(rule.rule_id)
            if (
                rule.scope.value == "client_override"
                and rule.client_id != client_id
            ):
                raise ValueError(
                    "client override rule must match its document client_id"
                )
            extract_markdown_section(markdown, rule.section_ref)
        return document, validated_rules
