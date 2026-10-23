"""Posting a supplier bill to Xero (task 5.5, architecture §12), one saved sub-step at a time:

    contact  the matched contact, else found by TRN or exact name, else created
    checked  no live bill with this supplier and invoice number is already in Xero (dup 5)
    bill     ACCPAY created as SUBMITTED (Awaiting Approval) with the idempotency key
    noted    history note: created by automation, who approved it

A sub-step whose result is in `progress` is not done again, so a crash resumes where it
stopped; the key makes a repeated create return the first bill instead of a second one.
"""

import re
from collections.abc import Callable, MutableMapping
from decimal import Decimal
from typing import Any

from app.adapters.accounting.base import (
    Posted,
    PostingBill,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.xero.client import XeroApiError, XeroClient
from app.domain.enums import AmountType

BILL = "ACCPAY"
AWAITING_APPROVAL = "SUBMITTED"  # rule 13: the MVP never authorises in Xero
LIVE_STATUSES = "DRAFT,SUBMITTED,AUTHORISED,PAID"  # deleted and voided bills do not count
LINE_AMOUNT_TYPES = {
    AmountType.EXCLUSIVE: "Exclusive",
    AmountType.INCLUSIVE: "Inclusive",
    AmountType.NO_TAX: "NoTax",
}
TRN = re.compile(r"^\d{15}$")


def post_bill(
    xero: XeroClient,
    bill: PostingBill,
    *,
    key: str,
    progress: MutableMapping[str, Any],
    save: Callable[[str], None],
) -> Posted:
    try:
        if "contact_id" not in progress:
            progress["contact_id"], progress["contact_created"] = _contact(xero, bill, key)
            save("contact")
        if "checked" not in progress:
            _refuse_if_already_in_xero(xero, bill, progress["contact_id"])
            progress["checked"] = True
            save("checked")
        if "bill_id" not in progress:
            created = xero.put(
                "Invoices", {"Invoices": [_bill(bill, progress["contact_id"])]}, key=key, unitdp=4
            )["Invoices"][0]
            progress["bill_id"], progress["total"] = created["InvoiceID"], str(created["Total"])
            save("bill")
        if "noted" not in progress:
            xero.put(
                f"Invoices/{progress['bill_id']}/History",
                {"HistoryRecords": [{"Details": bill.note}]},
                key=f"{key}-note",
            )
            progress["noted"] = True
            save("noted")
    except XeroApiError as exc:
        if exc.temporary:
            raise PostingUnavailable(str(exc), exc.retry_after) from exc
        raise PostingRefused("; ".join(exc.messages) or str(exc)) from exc
    return Posted(
        external_id=progress["bill_id"],
        total=Decimal(progress["total"]) if progress.get("total") else None,
        contact_external_id=progress["contact_id"],
        contact_created=bool(progress["contact_created"]),
    )


def _contact(xero: XeroClient, bill: PostingBill, key: str) -> tuple[str, bool]:
    """(Xero ContactID, created now?)"""
    if bill.supplier_external_id:
        return bill.supplier_external_id, False
    if bill.supplier_tax_number and TRN.match(bill.supplier_tax_number):
        same_trn = xero.get("Contacts", where=f'TaxNumber=="{bill.supplier_tax_number}"')
        if same_trn.get("Contacts"):
            return same_trn["Contacts"][0]["ContactID"], False
    wanted = bill.supplier_name.strip().casefold()
    for contact in xero.get("Contacts", searchTerm=bill.supplier_name).get("Contacts", []):
        if (contact.get("Name") or "").strip().casefold() == wanted:
            return contact["ContactID"], False
    new: dict[str, Any] = {"Name": bill.supplier_name.strip()}
    if bill.supplier_tax_number:
        new["TaxNumber"] = bill.supplier_tax_number
    created = xero.put("Contacts", {"Contacts": [new]}, key=f"{key}-contact")
    return created["Contacts"][0]["ContactID"], True


def _refuse_if_already_in_xero(xero: XeroClient, bill: PostingBill, contact_id: str) -> None:
    if not bill.invoice_number:
        return
    found = xero.get(
        "Invoices",
        ContactIDs=contact_id,
        InvoiceNumbers=bill.invoice_number,
        Statuses=LIVE_STATUSES,
        where=f'Type=="{BILL}"',
    ).get("Invoices", [])
    if found:
        raise PostingRefused(
            f"bill {bill.invoice_number} from this supplier is already in Xero "
            f"({found[0].get('Status', 'unknown status').lower()}): reject this one as a "
            "duplicate, or correct the invoice number"
        )


def _bill(bill: PostingBill, contact_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Type": BILL,
        "Contact": {"ContactID": contact_id},
        "Status": AWAITING_APPROVAL,
        "LineAmountTypes": LINE_AMOUNT_TYPES[bill.amount_type],
        "LineItems": [
            {
                "Description": line.description,
                "Quantity": line.quantity,
                "UnitAmount": line.unit_amount,
                "AccountCode": line.account_code,
                "TaxType": line.tax_code,
            }
            for line in bill.lines
        ],
    }
    optional = {
        "InvoiceNumber": bill.invoice_number,
        "Date": bill.invoice_date.isoformat() if bill.invoice_date else None,
        "DueDate": bill.due_date.isoformat() if bill.due_date else None,
        "CurrencyCode": bill.currency,
    }
    body |= {name: value for name, value in optional.items() if value}  # null, never invented
    return body
