"""Canonical invoice schema. Every part of the app uses it; no accounting-system types.

The schema records what the document says: a value that is not on the document is None, never
invented. Types and enums only. Whether a document has enough to post, and whether values have
the right format (TRN, currency code, decimals), is decided by the validation rules, which can
flag a field with a reason instead of failing the whole extraction.

Parse model output with `Invoice.model_validate_json(...)` so numbers become exact Decimals.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AmountType, DocumentType, TaxType

# Bump on any field change; stored with every extraction and correction.
INVOICE_SCHEMA_VERSION = 1

Money = Annotated[Decimal, Field(allow_inf_nan=False)]
Quantity = Annotated[Decimal, Field(allow_inf_nan=False)]


class _Schema(BaseModel):
    # forbid: the model cannot add fields, whatever text on the page tells it to.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Line(_Schema):
    description: str | None = None
    qty: Quantity | None = None
    unit_price: Money | None = None
    amount: Money
    account_code: str | None = Field(
        default=None, description="Limited at runtime to the client's active accounts."
    )
    tax_type: TaxType | None = None


class Invoice(_Schema):
    document_type: DocumentType

    vendor_name: str | None = None
    invoice_number: str | None = Field(
        default=None, description="As printed on the document. Null if the document has none."
    )
    invoice_date: date | None = None
    total: Money | None = None
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. AED.")

    vendor_trn: str | None = None
    recipient_name: str | None = None
    recipient_trn: str | None = None
    due_date: date | None = None
    po_number: str | None = None
    original_invoice_ref: str | None = Field(
        default=None, description="Credit/debit notes: number of the invoice being adjusted."
    )
    amount_type: AmountType | None = None
    subtotal: Money | None = None
    vat: Money | None = None

    lines: list[Line] = Field(default_factory=list)
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description="Field name -> exact source text the value was read from.",
    )
