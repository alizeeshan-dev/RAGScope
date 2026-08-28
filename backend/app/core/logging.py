"""Minimal structured logging with correlation IDs and recursive redaction."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from backend.app.tracing.redaction import configured_sensitive_values, redact

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "event_fields", {})
        safe = redact(fields, sensitive_values=configured_sensitive_values())
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
            "correlation_id": correlation_id.get(),
            "fields": safe,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def event(logger: logging.Logger, name: str, **fields: object) -> None:
    logger.info(name, extra={"event_fields": fields})
