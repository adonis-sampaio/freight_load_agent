"""Structured logging configuration with sensitive-field redaction."""

import logging
from typing import Any

import structlog
from pydantic import SecretStr

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


def _redact_value(key: str, value: Any) -> Any:
    normalized_key = key.lower().replace("-", "_")
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: _redact_value(str(item_key), item) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(key, item) for item in value)
    return value


def redact_sensitive_fields(
    _logger: logging.Logger, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Redact secret-like fields recursively before rendering a log event."""
    return {
        key: _redact_value(str(key), value)
        for key, value in event_dict.items()
    }


def configure_logging(log_level: str = "INFO") -> None:
    """Configure standard logging and structlog to emit JSON."""
    level = getattr(logging, log_level.upper(), None)
    if not isinstance(level, int):
        raise ValueError(f"Unsupported log level: {log_level}")

    logging.basicConfig(
        format="%(message)s",
        level=level,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            redact_sensitive_fields,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
