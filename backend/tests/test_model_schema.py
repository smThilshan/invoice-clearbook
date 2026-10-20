import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from app.domain.enums import DocumentType, TaxType
from app.domain.invoice import Invoice, Line
from app.pipeline.model_schema import build_model_schema


def walk(node: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


@pytest.fixture
def schema() -> dict[str, Any]:
    return build_model_schema()


def test_schema_has_no_constructs_that_break_small_model_decoding(schema: dict[str, Any]) -> None:
    keys = {key for key, _ in walk(schema)}

    assert not keys & {"anyOf", "oneOf", "allOf", "$ref", "$defs", "pattern", "default", "title"}


@pytest.mark.parametrize(
    ("model", "path"), [(Invoice, ()), (Line, ("properties", "lines", "items"))]
)
def test_every_field_present_and_required(
    schema: dict[str, Any], model: type[BaseModel], path: tuple[str, ...]
) -> None:
    node = schema
    for key in path:
        node = node[key]

    assert list(node["properties"]) == list(model.model_fields)
    assert node["required"] == list(model.model_fields)
    assert node["additionalProperties"] is False


def test_field_types(schema: dict[str, Any]) -> None:
    props = schema["properties"]
    line = props["lines"]["items"]["properties"]

    assert props["document_type"] == {
        "type": "string",
        "enum": [t.value for t in DocumentType],
    }
    assert props["total"] == {"type": ["number", "null"]}
    assert props["invoice_number"]["type"] == ["string", "null"]
    assert props["invoice_date"] == {"type": ["string", "null"], "format": "date"}
    assert props["evidence"]["additionalProperties"] == {"type": "string"}
    assert line["amount"] == {"type": "number"}
    assert line["tax_type"]["enum"] == [*(t.value for t in TaxType), None]


def test_field_descriptions_reach_the_model(schema: dict[str, Any]) -> None:
    assert "ISO 4217" in schema["properties"]["currency"]["description"]


def test_account_codes_limit_line_account_code() -> None:
    schema = build_model_schema(account_codes=["429", "720"])

    account = schema["properties"]["lines"]["items"]["properties"]["account_code"]

    assert account["type"] == ["string", "null"]
    assert account["enum"] == ["429", "720", None]


def test_answer_shaped_by_schema_validates_as_invoice(schema: dict[str, Any]) -> None:
    answer = dict.fromkeys(schema["properties"])
    answer |= {
        "document_type": "BILL",
        "total": 1050.10,
        "invoice_date": "2026-10-12",
        "lines": [
            {
                "description": "Paper",
                "qty": 10,
                "unit_price": 100.01,
                "amount": 1000.10,
                "account_code": None,
                "tax_type": "STANDARD_5",
            }
        ],
        "evidence": {"total": "Total AED 1,050.10"},
    }

    invoice = Invoice.model_validate_json(json.dumps(answer))

    assert invoice.total == Decimal("1050.10")
    assert invoice.lines[0].amount == Decimal("1000.10")


def test_extraction_schema_leaves_line_coding_to_the_coding_step() -> None:
    schema = build_model_schema(line_coding=False)
    line = schema["properties"]["lines"]["items"]

    assert "account_code" not in line["properties"]
    assert "tax_type" not in line["properties"]
    coding = ("account_code", "tax_type")
    assert line["required"] == [name for name in Line.model_fields if name not in coding]
