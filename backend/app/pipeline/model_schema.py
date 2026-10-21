"""JSON schema sent to the model, generated from the canonical `Invoice`.

Pydantic's own schema is for validation (anyOf, regex patterns, defaults, $ref). Turned into a
decoding grammar it steers a small model to `null` for every field (smoke test, task 1.7). The
model gets this flat version instead: plain types, inline enums, every field required so each one
is decided, no patterns (Ollama ignores them)/defaults/refs. Dates are plain text copied as
printed; our own parser turns them into dates (decision 28). The answer is then validated with the
strict `Invoice`.

Generated, never hand-written, so it cannot drift from `Invoice`; an unsupported field type
raises, forcing a decision when the schema grows.
"""

import types
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel

from app.domain.invoice import Invoice, Line

JsonSchema = dict[str, Any]

# Line coding is assigned by the coding step (task 3.6), not read off the document.
LINE_CODING_FIELDS = frozenset({(Line, "account_code"), (Line, "tax_type")})

# Decision 28: forced to YYYY-MM-DD, the model copied "4/5/2026" digit by digit into the year
# ("4520-05-04"). It copies the date as printed; `extract.parse_answer_dates` converts it.
PRINTED_DATE: JsonSchema = {
    "type": "string",
    "description": "Exactly as printed, e.g. 4/5/2026 or 02 Sep 2025. Do not reorder or convert.",
}


def build_model_schema(
    account_codes: Sequence[str] | None = None, *, line_coding: bool = True
) -> JsonSchema:
    """Schema for `Invoice`. With `account_codes`, line account codes are limited to them.
    `line_coding=False` leaves account/tax codes out (extraction: fewer tokens to write)."""
    overrides: dict[tuple[type[BaseModel], str], JsonSchema] = {}
    if account_codes is not None:
        overrides[(Line, "account_code")] = {
            "type": ["string", "null"],
            "enum": [*account_codes, None],
        }
    omit = frozenset() if line_coding else LINE_CODING_FIELDS
    return _object_schema(Invoice, overrides, omit)


def _object_schema(
    model: type[BaseModel],
    overrides: dict[tuple[type[BaseModel], str], JsonSchema],
    omit: frozenset[tuple[type[BaseModel], str]] = frozenset(),
) -> JsonSchema:
    properties: dict[str, JsonSchema] = {}
    for name, field in model.model_fields.items():
        if (model, name) in omit:
            continue  # nullable in Invoice, so the answer still validates
        schema = overrides.get((model, name)) or _type_schema(field.annotation, overrides, omit)
        if field.description:
            schema = {**schema, "description": field.description}
        properties[name] = schema
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _type_schema(
    annotation: Any,
    overrides: dict[tuple[type[BaseModel], str], JsonSchema],
    omit: frozenset[tuple[type[BaseModel], str]] = frozenset(),
) -> JsonSchema:
    if get_origin(annotation) is Annotated:
        return _type_schema(get_args(annotation)[0], overrides, omit)

    if get_origin(annotation) in (Union, types.UnionType):
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) != 1:
            raise TypeError(f"unsupported union in model schema: {annotation}")
        return _nullable(_type_schema(members[0], overrides, omit))

    if get_origin(annotation) is list:
        (item,) = get_args(annotation)
        return {"type": "array", "items": _type_schema(item, overrides, omit)}

    if get_origin(annotation) is dict:
        key, value = get_args(annotation)
        if key is not str:
            raise TypeError(f"unsupported dict key in model schema: {annotation}")
        return {"type": "object", "additionalProperties": _type_schema(value, overrides, omit)}

    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return _object_schema(annotation, overrides, omit)
        if issubclass(annotation, StrEnum):
            return {"type": "string", "enum": [member.value for member in annotation]}
        if issubclass(annotation, Decimal):
            return {"type": "number"}
        if issubclass(annotation, date):
            return dict(PRINTED_DATE)
        if issubclass(annotation, str):
            return {"type": "string"}

    raise TypeError(f"unsupported type in model schema: {annotation}")


def _nullable(schema: JsonSchema) -> JsonSchema:
    nullable = {**schema, "type": [schema["type"], "null"]}
    if "enum" in schema:
        nullable["enum"] = [*schema["enum"], None]
    return nullable
