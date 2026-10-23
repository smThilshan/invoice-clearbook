"""Posting a supplier bill to Xero (task 5.5, architecture §12), one saved sub-step at a time:

    due_date as printed; else from the supplier's, then the organisation's, bill payment terms
             in Xero (decision 40); none: back to review before anything is created
    contact  the matched contact, else found by TRN or exact name, else created
    checked  no live bill with this supplier and invoice number is already in Xero (dup 5)
    bill     ACCPAY created as SUBMITTED (Awaiting Approval) with the idempotency key
    attached the original file on the bill (decision 41); if Xero will not take it, the bill
             stays and the note says so
    noted    history note: created by automation, who approved it

A sub-step whose result is in `progress` is not done again, so a crash resumes where it
stopped; the key makes a repeated create return the first bill instead of a second one.
"""

import re
from collections.abc import Callable, MutableMapping
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from app.adapters.accounting.base import (
    Posted,
    PostingBill,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.xero.client import XeroApiError, XeroClient
from app.adapters.accounting.xero.payment_terms import due_date_from_terms
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
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # Xero's limit for a file attached through the API
NO_DUE_DATE = (
    "no due date is printed and Xero has no bill payment terms for this supplier or the "
    "organisation: type the due date, or set default bill terms in Xero (Settings, Invoice "
    "settings, Default settings, Bills)"
)


def post_bill(
    xero: XeroClient,
    bill: PostingBill,
    *,
    key: str,
    progress: MutableMapping[str, Any],
    save: Callable[[str], None],
) -> Posted:
    try:
        if "due_date" not in progress:
            found = progress.get("contact_id") or _find_contact(xero, bill)  # reads only
            progress["due_date"], progress["due_date_from"] = _due_date(xero, bill, found)
            if found:
                progress["contact_id"], progress["contact_created"] = found, False
            save("due_date")
        if "contact_id" not in progress:
            progress["contact_id"] = _create_contact(xero, bill, key)
            progress["contact_created"] = True
            save("contact")
        if "checked" not in progress:
            _refuse_if_already_in_xero(xero, bill, progress["contact_id"])
            progress["checked"] = True
            save("checked")
        if "bill_id" not in progress:
            body = _bill(bill, progress["contact_id"], progress["due_date"])
            created = xero.put("Invoices", {"Invoices": [body]}, key=key, unitdp=4)["Invoices"][0]
            progress["bill_id"], progress["total"] = created["InvoiceID"], str(created["Total"])
            save("bill")
        if "attached" not in progress:
            progress["attached"] = _attach(xero, bill, progress["bill_id"])
            save("attached")
        if "noted" not in progress:
            xero.put(
                f"Invoices/{progress['bill_id']}/History",
                {"HistoryRecords": [{"Details": _note(bill, progress)}]},
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
        due_date_from=progress["due_date_from"],
        attached=bool(progress["attached"]),
    )


def _find_contact(xero: XeroClient, bill: PostingBill) -> str | None:
    """The supplier's Xero ContactID: the matched contact, else by TRN, else by exact name."""
    if bill.supplier_external_id:
        return bill.supplier_external_id
    if bill.supplier_tax_number and TRN.match(bill.supplier_tax_number):
        same_trn = xero.get("Contacts", where=f'TaxNumber=="{bill.supplier_tax_number}"')
        if same_trn.get("Contacts"):
            return str(same_trn["Contacts"][0]["ContactID"])
    wanted = bill.supplier_name.strip().casefold()
    for contact in xero.get("Contacts", searchTerm=bill.supplier_name).get("Contacts", []):
        if (contact.get("Name") or "").strip().casefold() == wanted:
            return str(contact["ContactID"])
    return None


def _create_contact(xero: XeroClient, bill: PostingBill, key: str) -> str:
    new: dict[str, Any] = {"Name": bill.supplier_name.strip()}
    if bill.supplier_tax_number:
        new["TaxNumber"] = bill.supplier_tax_number
    created = xero.put("Contacts", {"Contacts": [new]}, key=f"{key}-contact")
    return str(created["Contacts"][0]["ContactID"])


def _due_date(xero: XeroClient, bill: PostingBill, contact_id: str | None) -> tuple[str, str]:
    """(ISO due date, where it came from: "document" | "supplier terms" | "organisation terms")."""
    if bill.due_date:
        return bill.due_date.isoformat(), "document"
    if bill.invoice_date is None:
        raise PostingRefused("no due date or bill date is printed: type the due date")
    if contact_id:
        contact = xero.get(f"Contacts/{contact_id}").get("Contacts", [{}])[0]
        found = _from_terms(bill.invoice_date, contact)
        if found:
            return found.isoformat(), "supplier terms"
    organisation = xero.get("Organisation").get("Organisations", [{}])[0]
    found = _from_terms(bill.invoice_date, organisation)
    if found:
        return found.isoformat(), "organisation terms"
    raise PostingRefused(NO_DUE_DATE)


def _from_terms(bill_date: date, holder: dict[str, Any]) -> date | None:
    return due_date_from_terms(bill_date, (holder.get("PaymentTerms") or {}).get("Bills"))


def _attach(xero: XeroClient, bill: PostingBill, bill_id: str) -> bool:
    """Attach the original once (a retry finds it there). False = not attached."""
    file = bill.attachment
    if file is None:
        return False
    if len(file.content) > MAX_ATTACHMENT_BYTES:
        return False  # Xero would refuse it; the original stays in Missan's folders
    resource = f"Invoices/{bill_id}/Attachments"
    there = xero.get(resource).get("Attachments", [])
    if any(a.get("FileName") == file.file_name for a in there):
        return True
    try:
        xero.put_file(f"{resource}/{quote(file.file_name)}", file.content, file.mime_type)
    except XeroApiError as exc:
        if exc.temporary:
            raise
        return False  # refused (type or size): the bill itself is fine
    return True


def _note(bill: PostingBill, progress: MutableMapping[str, Any]) -> str:
    note = bill.note
    if progress["due_date_from"] != "document":
        note += (
            f" No due date was printed: it was set from the {progress['due_date_from']} in Xero."
        )
    if bill.attachment and not progress["attached"]:
        note += " The original file could not be attached: it is in Missan's folders."
    return note


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


def _bill(bill: PostingBill, contact_id: str, due_date: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Type": BILL,
        "Contact": {"ContactID": contact_id},
        "Status": AWAITING_APPROVAL,
        "DueDate": due_date,  # Xero requires one on a bill
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
        "CurrencyCode": bill.currency,
    }
    body |= {name: value for name, value in optional.items() if value}  # null, never invented
    return body
