import json
import logging

import pytest

from app.core.logging import JsonFormatter, RedactingFormatter, redact


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("vendor TRN 100123456700003 ok", "vendor TRN [TRN] ok"),
        ("TRN ١٠٠١٢٣٤٥٦٧٠٠٠٠٣", "TRN [TRN]"),  # Arabic-Indic digits
        ("iban AE070331234567890123456 set", "iban [IBAN] set"),
        ("iban AE07 0331 2345 6789 0123 456 set", "iban [IBAN] set"),
    ],
)
def test_redact_masks_trn_and_iban(raw: str, expected: str) -> None:
    assert redact(raw) == expected


@pytest.mark.parametrize(
    "safe",
    [
        "document 42 moved to READ",
        "invoice INV-00291",
        "14 digits 10012345670000",
        "16 digits 1001234567000031",
    ],
)
def test_redact_leaves_other_text(safe: str) -> None:
    assert redact(safe) == safe


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


def test_text_formatter_masks_interpolated_args() -> None:
    line = RedactingFormatter("%(message)s").format(_record("trn=%s", "100123456700003"))

    assert line == "trn=[TRN]"


def test_json_formatter_emits_masked_json() -> None:
    line = JsonFormatter().format(_record("trn=%s", "100123456700003"))

    entry = json.loads(line)
    assert entry["msg"] == "trn=[TRN]"
    assert entry["level"] == "INFO"
    assert entry["ts"].endswith("+00:00")
