"""Posting a supplier bill to Zoho Books (decision 74), one saved sub-step at a time:

    contact  the matched vendor, else found by TRN or exact name, else created
    checked  not already in Zoho: our reference (a retry finds its own bill), or this vendor's
             bill number (a duplicate goes back to review)
    create_sent  saved right before the create request: the bill may exist from now on
    bill     created (Zoho makes it a draft), our outbox key as its reference number
    submitted  submitted for approval: Pending Approval, never final (as rule 13 for Xero);
             an organisation without bill approvals keeps it as a draft
    attached the original file on the bill; if Zoho will not take it, the bill stays
    noted    a comment: created by automation, who approved it

Zoho has no idempotency key: our reference is searched before every create, so a crash after
the create finds the bill instead of making a second one. A sub-step whose result is in
`progress` is not done again.
"""

from collections.abc import Callable, MutableMapping
from decimal import Decimal
from typing import Any

from app.adapters.accounting.base import (
    CREATE_SENT,
    Posted,
    PostingAccessDenied,
    PostingBill,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.zoho.client import ZohoApiError, ZohoClient
from app.adapters.accounting.zoho.master_data import NO_VAT
from app.adapters.accounting.zoho.vendors import create_vendor, find_vendor
from app.domain.enums import AmountType

PENDING_APPROVAL = "pending_approval"
FROM_TERMS = "payment terms in Zoho Books"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ATTACHABLE = {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/bmp"}
NEEDS_NUMBER = (
    "Zoho Books needs a bill number: type the number printed on the receipt (for example a "
    "booking or trip number)"
)


def post_bill(
    zoho: ZohoClient,
    bill: PostingBill,
    *,
    key: str,
    progress: MutableMapping[str, Any],
    save: Callable[[str], None],
) -> Posted:
    if not bill.invoice_number:
        raise PostingRefused(NEEDS_NUMBER)  # the review asks first; this is the safety net
    try:
        if "contact_id" not in progress:
            found = find_vendor(
                zoho, bill.supplier_name, bill.supplier_tax_number, bill.supplier_external_id
            )
            progress["contact_id"] = found or create_vendor(
                zoho, bill.supplier_name, bill.supplier_tax_number
            )
            progress["contact_created"] = found is None
            save("contact")
        if "checked" not in progress:
            ours = _ours(zoho, key)
            if ours:
                progress["bill_id"] = ours  # created by an earlier attempt of this row
            else:
                _refuse_if_already_in_zoho(zoho, bill, progress["contact_id"])
            progress["checked"] = True
            save("checked")
        if "bill_id" not in progress:
            found = _ours(zoho, key) if CREATE_SENT in progress else None
            if found is None:
                progress[CREATE_SENT] = True
                save(CREATE_SENT)
                body = _bill(bill, progress["contact_id"], account_ids(zoho), key)
                found = str(zoho.post("bills", body)["bill"]["bill_id"])
            progress["bill_id"] = found
            save("bill")
        if "submitted" not in progress:
            progress["submitted"] = _submit(zoho, progress["bill_id"])
            save("submitted")
        if "attached" not in progress:
            progress["attached"] = _attach(zoho, bill, progress["bill_id"])
            save("attached")
        if "noted" not in progress:
            zoho.post(
                f"bills/{progress['bill_id']}/comments", {"description": _note(bill, progress)}
            )
            progress["noted"] = True
            save("noted")
        total = zoho.get(f"bills/{progress['bill_id']}")["bill"].get("total")
    except ZohoApiError as exc:
        if exc.access:
            raise PostingAccessDenied(str(exc), reconnect=exc.access == 403) from exc
        if exc.temporary:
            raise PostingUnavailable(str(exc), exc.retry_after) from exc
        raise PostingRefused(str(exc)) from exc
    return Posted(
        external_id=progress["bill_id"],
        total=Decimal(str(total)) if total is not None else None,
        contact_external_id=progress["contact_id"],
        contact_created=bool(progress["contact_created"]),
        due_date_from="document" if bill.due_date else FROM_TERMS,
        attached=bool(progress["attached"]),
    )


def _ours(zoho: ZohoClient, key: str) -> str | None:
    """The bill this outbox row already created, found by its reference number."""
    found = zoho.get("bills", reference_number=key).get("bills", [])
    return str(found[0]["bill_id"]) if found else None


def _refuse_if_already_in_zoho(zoho: ZohoClient, bill: PostingBill, vendor_id: str) -> None:
    found = zoho.get("bills", bill_number=bill.invoice_number, vendor_id=vendor_id).get("bills", [])
    live = [b for b in found if b.get("status") != "void"]
    if live:
        raise PostingRefused(
            f"bill {bill.invoice_number} from this supplier is already in Zoho Books "
            f"({live[0].get('status', 'unknown status').replace('_', ' ')}): reject this one as "
            "a duplicate, or correct the invoice number"
        )


def account_ids(zoho: ZohoClient) -> dict[str, str]:
    """Account code or ID -> Zoho account ID: a line holds the code, or the ID when Zoho has
    no code for the account (master data, CODE_IS_REFERENCE)."""
    accounts: dict[str, str] = {}
    for account in zoho.pages("chartofaccounts", "chartofaccounts", filter_by="AccountType.All"):
        account_id = str(account["account_id"])
        accounts[account_id] = account_id
        if account.get("account_code"):
            accounts[str(account["account_code"])] = account_id
    return accounts


def _bill(bill: PostingBill, vendor_id: str, accounts: dict[str, str], key: str) -> dict[str, Any]:
    lines = []
    for number, line in enumerate(bill.lines, 1):
        account_id = accounts.get(line.account_code)
        if account_id is None:
            raise PostingRefused(
                f"line {number}: account {line.account_code} is not in Zoho Books: sync the "
                "master data and choose the account again"
            )
        item: dict[str, Any] = {
            "account_id": account_id,
            "rate": line.unit_amount,
            "quantity": line.quantity,
        }
        if line.description:
            item["description"] = line.description
        if line.tax_code != NO_VAT:
            item["tax_id"] = line.tax_code
        lines.append(item)
    body: dict[str, Any] = {
        "vendor_id": vendor_id,
        "bill_number": bill.invoice_number,
        "reference_number": key,  # found again by a retry: Zoho has no idempotency key
        "is_inclusive_tax": bill.amount_type is AmountType.INCLUSIVE,
        "line_items": lines,
    }
    optional = {
        "date": bill.invoice_date.isoformat() if bill.invoice_date else None,
        "due_date": bill.due_date.isoformat() if bill.due_date else None,  # else Zoho's terms
    }
    return body | {name: value for name, value in optional.items() if value}


def _submit(zoho: ZohoClient, bill_id: str) -> bool:
    """Submit for approval; False = left as a draft (the organisation has no bill approvals)."""
    if zoho.get(f"bills/{bill_id}")["bill"].get("status") == PENDING_APPROVAL:
        return True  # a retry: already submitted
    try:
        zoho.post(f"bills/{bill_id}/submit")
    except ZohoApiError as exc:
        if exc.temporary:
            raise
        return False
    return True


def _attach(zoho: ZohoClient, bill: PostingBill, bill_id: str) -> bool:
    """Attach the original once (a retry finds it there). False = not attached."""
    file = bill.attachment
    if file is None or file.mime_type not in ATTACHABLE:
        return False
    if len(file.content) > MAX_ATTACHMENT_BYTES:
        return False  # the original stays in Missan's folders
    there = zoho.get(f"bills/{bill_id}")["bill"].get("documents") or []
    if any(d.get("file_name") == file.file_name for d in there):
        return True
    try:
        zoho.post_file(f"bills/{bill_id}/attachment", file.file_name, file.content, file.mime_type)
    except ZohoApiError as exc:
        if exc.temporary:
            raise
        return False  # refused (type or size): the bill itself is fine
    return True


def _note(bill: PostingBill, progress: MutableMapping[str, Any]) -> str:
    note = bill.note
    if not bill.due_date:
        note += f" No due date was printed: it was set from the {FROM_TERMS}."
    if not progress["submitted"]:
        note += " Left as a draft: bill approvals are off in this organisation."
    if bill.attachment and not progress["attached"]:
        note += " The original file could not be attached: it is in Missan's folders."
    return note


NOT_FOUND = 1002  # Zoho: "Bill does not exist." (deleted)


def is_deleted(zoho: ZohoClient, bill_id: str) -> bool:
    """Zoho removes a deleted bill (404, code 1002) and keeps a voided one as "void"."""
    try:
        bill = zoho.get(f"bills/{bill_id}").get("bill") or {}
    except ZohoApiError as exc:
        if exc.status == 404 or exc.code == NOT_FOUND:
            return True
        if exc.access:
            raise PostingAccessDenied(str(exc), reconnect=exc.access == 403) from exc
        raise PostingUnavailable(str(exc), exc.retry_after) from exc
    return bill.get("status") == "void"
