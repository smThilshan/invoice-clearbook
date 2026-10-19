"""Logging setup: readable text in dev, JSON lines in prod.

Rule: never log invoice amounts, TRNs or vendor bank details. That is enforced by not passing
them to loggers. As a safety net every formatted line is masked for TRN- and IBAN-shaped values;
amounts cannot be detected reliably, so for them the rule is the only protection.
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime

# Applied in order: IBAN first so its digit run is not half-masked as a TRN.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b"), "[IBAN]"),
    (re.compile(r"(?<!\d)\d{15}(?!\d)"), "[TRN]"),  # \d also matches Arabic-Indic digits
)

_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, str] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, ensure_ascii=False))


def configure_logging(level: str, *, json_output: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_output else RedactingFormatter(_TEXT_FORMAT))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Route uvicorn through the same handler so its lines are formatted and masked too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
