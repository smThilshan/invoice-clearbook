"""What a supplier's other documents prove (decisions 84, 85), read once per check: the VAT
type it uses and the way it writes dates. Only proof counts, never a value one of these
decisions filled in, so a guess cannot copy itself from one document to the next."""

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import AmountType, DocumentStatus
from app.domain.invoice import Invoice
from app.models import Document
from app.pipeline.dates import DateOrder, date_order
from app.pipeline.verify.duplicates import vendor_key
from app.pipeline.verify.vat_type import vat_type_from_amounts

LIMIT = 50  # documents read, the latest first
DECIDED = (DocumentStatus.APPROVED, DocumentStatus.POSTING, DocumentStatus.POSTED,
           DocumentStatus.SYNCED, DocumentStatus.POST_ERROR)  # fmt: skip


@dataclass(frozen=True, slots=True)
class SupplierHistory:
    vat_type: AmountType | None = None  # the type its documents prove, when they all agree
    date_order: DateOrder | None = None  # day or month first, when its dates prove it


NONE = SupplierHistory()


def load_supplier_history(
    session: Session, document: Document, invoice: Invoice
) -> SupplierHistory:
    key = vendor_key(invoice)
    if key is None:
        return NONE
    others = session.scalars(
        select(Document)
        .where(Document.client_id == document.client_id, Document.vendor_key == key,
               Document.id != document.id, Document.invoice.is_not(None))
        .order_by(Document.created_at.desc())
        .limit(LIMIT)
    )  # fmt: skip
    vat_types: set[AmountType] = set()
    date_texts: list[str | None] = []
    for other in others:
        try:
            earlier = Invoice.model_validate(other.invoice)
        except ValidationError:
            continue
        date_texts.append(earlier.evidence.get("invoice_date"))
        chosen = earlier.amount_type
        if chosen is not None and _proven(other, earlier, chosen):
            vat_types.add(chosen)
    vat_type = vat_types.pop() if len(vat_types) == 1 else None
    return SupplierHistory(vat_type, date_order(date_texts))


def _proven(document: Document, invoice: Invoice, chosen: AmountType) -> bool:
    """The accountant chose or confirmed it, the document was approved, or its amounts show it."""
    decided = document.status in DECIDED or "amount_type" in (document.verified_fields or ())
    return decided or vat_type_from_amounts(invoice) is chosen
