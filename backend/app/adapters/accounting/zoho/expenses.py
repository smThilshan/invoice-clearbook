"""Posting an expense to Zoho Books as an expense (decision 89), one saved sub-step at a time:

    priced   before anything is sent: Zoho works out VAT line by line, so the VAT it would give
             must be the printed VAT to the fils; otherwise nothing is sent, back to review
    contact  the matched vendor, else found by TRN or exact name, else created; none when the
             expense names no supplier (Zoho takes an expense without one)
    checked  not already in Zoho: our key in the reference (a retry finds its own expense), or
             this vendor's expense of the same date and total (a duplicate goes back to review)
    create_sent  saved right before the create request: the expense may exist from now on
    expense  created, paid through the chosen account: final at once (Zoho has no approval or
             draft for expenses), the note in its description
    attached the original as its receipt; if Zoho will not take it, the expense stays

What the trial on 2026-11-09 proved: an expense with VAT needs `tax_treatment` on the expense
(the emirate is then the organisation's own); without `paid_through_account_id` Zoho puts it on
"Undeposited Funds", so it is always sent; Zoho does not refuse a duplicate reference; the
receipt goes as form field `receipt`; a deleted expense reads 404, code 1002; the reference
takes fewer than 50 characters. Zoho has no idempotency key: our key (without its dashes, 32
characters) ends the reference and is searched before every create.
"""

from collections.abc import Callable, MutableMapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.adapters.accounting.base import (
    CREATE_SENT,
    Posted,
    PostingAccessDenied,
    PostingExpense,
    PostingLine,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.zoho.bills import (
    ATTACHABLE,
    MAX_ATTACHMENT_BYTES,
    NOT_FOUND,
    account_ids,
)
from app.adapters.accounting.zoho.client import ZohoApiError, ZohoClient
from app.adapters.accounting.zoho.master_data import NO_VAT
from app.adapters.accounting.zoho.vendors import create_vendor, find_vendor
from app.domain.enums import AmountType

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
MAX_TEXT = 100  # Zoho's limit on an expense's description and line description
MAX_REFERENCE = 49  # "less than 50 characters", Zoho said in the test (its docs say 100)
VAT_REGISTERED = "vat_registered"


def post_expense(
    zoho: ZohoClient,
    expense: PostingExpense,
    *,
    key: str,
    progress: MutableMapping[str, Any],
    save: Callable[[str], None],
) -> Posted:
    try:
        accounts = account_ids(zoho)
        if "priced" not in progress:
            _refuse_unless_vat_matches(expense, _tax_rates(zoho))
            progress["priced"] = True
            save("priced")
        if "contact_id" not in progress:
            progress["contact_id"], progress["contact_created"] = _vendor(zoho, expense)
            save("contact")
        if "checked" not in progress:
            ours = _ours(zoho, key)
            if ours:
                progress["expense_id"] = ours  # created by an earlier attempt of this row
            else:
                _refuse_if_already_in_zoho(zoho, expense, progress["contact_id"])
            progress["checked"] = True
            save("checked")
        if "expense_id" not in progress:
            found = _ours(zoho, key) if CREATE_SENT in progress else None
            if found is None:
                progress[CREATE_SENT] = True
                save(CREATE_SENT)
                body = _expense(expense, progress["contact_id"], accounts, key)
                found = str(zoho.post("expenses", body)["expense"]["expense_id"])
            progress["expense_id"] = found
            save("expense")
        if "attached" not in progress:
            progress["attached"] = _attach(zoho, expense, progress["expense_id"])
            save("attached")
        total = zoho.get(f"expenses/{progress['expense_id']}")["expense"].get("total")
    except ZohoApiError as exc:
        if exc.access:
            raise PostingAccessDenied(str(exc), reconnect=exc.access == 403) from exc
        if exc.temporary:
            raise PostingUnavailable(str(exc), exc.retry_after) from exc
        raise PostingRefused(str(exc)) from exc
    return Posted(
        external_id=progress["expense_id"],
        total=Decimal(str(total)) if total is not None else None,
        contact_external_id=progress["contact_id"],
        contact_created=bool(progress["contact_created"]),
        attached=bool(progress["attached"]),
    )


def zoho_vat(
    lines: Sequence[PostingLine], amount_type: AmountType, rates: dict[str, Decimal]
) -> Decimal:
    """The VAT Zoho works out: line by line, each rounded to the fils (12 till items of a
    receipt printing 10.03 gave 10.04 in the trial). `rates`: tax ID -> percentage."""
    vat = Decimal(0)
    for line in lines:
        rate = rates.get(line.tax_code, Decimal(0)) if line.tax_code != NO_VAT else Decimal(0)
        amount = _amount(line)
        base = (
            amount * rate / (HUNDRED + rate)
            if amount_type is AmountType.INCLUSIVE
            else amount * rate / HUNDRED
        )
        vat += base.quantize(CENT, rounding=ROUND_HALF_UP)
    return vat


def is_deleted(zoho: ZohoClient, expense_id: str) -> bool:
    """Zoho removes a deleted expense (404, code 1002)."""
    try:
        zoho.get(f"expenses/{expense_id}")
    except ZohoApiError as exc:
        if exc.status == 404 or exc.code == NOT_FOUND:
            return True
        if exc.access:
            raise PostingAccessDenied(str(exc), reconnect=exc.access == 403) from exc
        raise PostingUnavailable(str(exc), exc.retry_after) from exc
    return False


def _refuse_unless_vat_matches(expense: PostingExpense, rates: dict[str, Decimal]) -> None:
    vat = zoho_vat(expense.lines, expense.amount_type, rates)
    if vat != expense.vat:
        raise PostingRefused(
            f"Zoho Books would work out VAT {vat:.2f}, but the document says {expense.vat:.2f} "
            "(Zoho rounds the VAT of each line): check the lines and their VAT, then approve "
            "again"
        )


def _vendor(zoho: ZohoClient, expense: PostingExpense) -> tuple[str | None, bool]:
    """(vendor ID or None, created now)."""
    name = (expense.supplier_name or "").strip()
    if not name and not expense.supplier_external_id:
        return None, False
    found = find_vendor(zoho, name, expense.supplier_tax_number, expense.supplier_external_id)
    if found:
        return found, False
    return create_vendor(zoho, name, expense.supplier_tax_number), True


def _ours(zoho: ZohoClient, key: str) -> str | None:
    """The expense this outbox row already created: our key is in its reference."""
    found = zoho.get("expenses", reference_number_contains=_compact(key)).get("expenses", [])
    return str(found[0]["expense_id"]) if found else None


def _refuse_if_already_in_zoho(
    zoho: ZohoClient, expense: PostingExpense, vendor_id: str | None
) -> None:
    """The same supplier's expense on the same day for the same total, or with the same printed
    number. Without a supplier or a number nothing is compared: two coffees of the same price on
    one day are not duplicates (our own duplicate checks ran before approval)."""
    if vendor_id is None and not expense.reference:
        return
    search: dict[str, Any] = {"date": expense.expense_date.isoformat()}
    if vendor_id is not None:
        search["vendor_id"] = vendor_id
    total = sum((_amount(line) for line in expense.lines), Decimal(0))
    if expense.amount_type is not AmountType.INCLUSIVE:
        total += expense.vat
    for found in zoho.get("expenses", **search).get("expenses", []):
        same_total = vendor_id is not None and Decimal(str(found.get("total"))) == total
        same_number = bool(expense.reference) and (found.get("reference_number") or "").startswith(
            f"{expense.reference} "
        )
        if same_total or same_number:
            raise PostingRefused(
                f"an expense of {total:.2f} on {expense.expense_date:%d %b %Y} from this supplier "
                "is already in Zoho Books: reject this one as a duplicate, or correct it"
            )


def _tax_rates(zoho: ZohoClient) -> dict[str, Decimal]:
    taxes = zoho.get("settings/taxes").get("taxes") or []
    return {str(t["tax_id"]): Decimal(str(t.get("tax_percentage") or 0)) for t in taxes}


def _expense(
    expense: PostingExpense, vendor_id: str | None, accounts: dict[str, str], key: str
) -> dict[str, Any]:
    paid_through = accounts.get(expense.paid_through)
    if paid_through is None:
        raise PostingRefused(
            f"the paid-through account {expense.paid_through} is not in Zoho Books: sync the "
            "master data and choose it again"
        )
    lines = []
    for number, line in enumerate(expense.lines, 1):
        account_id = accounts.get(line.account_code)
        if account_id is None:
            raise PostingRefused(
                f"line {number}: account {line.account_code} is not in Zoho Books: sync the "
                "master data and choose the account again"
            )
        item: dict[str, Any] = {"account_id": account_id, "amount": _amount(line)}
        if line.description:
            item["description"] = _short(line.description)
        if line.tax_code != NO_VAT:
            item["tax_id"] = line.tax_code
        lines.append(item)
    body: dict[str, Any] = {
        "account_id": lines[0]["account_id"],
        "date": expense.expense_date.isoformat(),
        "paid_through_account_id": paid_through,  # never left out: Zoho would choose one
        "reference_number": _reference(expense.reference, key),
        "is_inclusive_tax": expense.amount_type is AmountType.INCLUSIVE,
        "description": _short(expense.note),
        "line_items": lines,
    }
    if vendor_id is not None:
        body["vendor_id"] = vendor_id
    if any("tax_id" in item for item in lines):
        body["tax_treatment"] = VAT_REGISTERED  # Zoho refuses VAT without it
    return body


def _attach(zoho: ZohoClient, expense: PostingExpense, expense_id: str) -> bool:
    """Attach the original as the receipt once (a retry finds it there). False = not attached."""
    file = expense.attachment
    if file is None or file.mime_type not in ATTACHABLE:
        return False
    if len(file.content) > MAX_ATTACHMENT_BYTES:
        return False  # the original stays in Missan's folders
    there = zoho.get(f"expenses/{expense_id}")["expense"].get("expense_receipt_name")
    if there == file.file_name:
        return True
    try:
        zoho.post_file(f"expenses/{expense_id}/receipt", file.file_name, file.content,
                       file.mime_type, field="receipt")  # fmt: skip
    except ZohoApiError as exc:
        if exc.temporary:
            raise
        return False  # refused (type or size): the expense itself is fine
    return True


def _amount(line: PostingLine) -> Decimal:
    return (line.quantity * line.unit_amount).quantize(CENT, rounding=ROUND_HALF_UP)


def _reference(printed: str | None, key: str) -> str:
    """The printed number, then our key (found again by a retry), within Zoho's limit: a long
    number is cut, never the key."""
    ours = _compact(key)
    if not printed:
        return ours
    return f"{printed.strip()[: MAX_REFERENCE - len(ours) - 1]} {ours}"


def _compact(key: str) -> str:
    return key.replace("-", "")


def _short(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"
