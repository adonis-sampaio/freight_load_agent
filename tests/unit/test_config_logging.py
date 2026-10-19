import logging

from config.logging import redact_sensitive_fields
from config.settings import Settings


def test_settings_store_api_key_as_secret() -> None:
    settings = Settings(llm_api_key="provided-secret")

    assert str(settings.llm_api_key) == "**********"


def test_structured_logging_redacts_sensitive_fields_recursively() -> None:
    event = redact_sensitive_fields(
        logging.getLogger("test"),
        "info",
        {
            "llm_api_key": "provided-secret",
            "context": {"authorization": "Bearer token-value", "load_id": "load-1"},
        },
    )

    assert event == {
        "llm_api_key": "[REDACTED]",
        "context": {
            "authorization": "[REDACTED]",
            "load_id": "load-1",
        },
    }
