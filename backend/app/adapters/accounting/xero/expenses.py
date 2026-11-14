"""Posting an expense to Xero as Spend Money (decision 97, as decision 89 for Zoho), one saved
sub-step at a time:

    priced   before anything is sent: it is paid from one of the organisation's active bank
             accounts in the expense's own currency, and the VAT Xero works out (line by line,
             each rounded to the fils) is the printed VAT; otherwise back to review
    contact  the matched contact, else found by TRN or exact name, else created; Xero takes no
             Spend Money without one, so an expense naming no supplier goes back to review
    checked  not already in Xero: this supplier's live Spend Money of the same date and total,
             or with the same printed number (a duplicate goes back to review)
    create_sent  saved right before the create request: the expense may exist from now on
    expense  SPEND created with the idempotency key: final at once (Xero has no draft for it),
             never marked reconciled (the accountant matches it to the bank line in Xero)
    attached the original; if Xero will not take it, the expense stays
    noted    history note: created by automation, who approved it

What the trial on 2026-11-13 proved (demo organisation, made-up data): a bank account may have
no code, so it is sent by its AccountID; no reference is fine; no contact is refused; DRAFT is
refused; a duplicate reference is not refused; Xero rounds the VAT of each line (12 items of
0.87 gave 0.48, not 0.52) and refuses a VAT amount sent for a line; a USD expense on an AED
account was saved as AED without a word; one marked reconciled cannot be deleted through the
API; a search returns deleted ones too; the same Idempotency-Key returns the first one.
"""

from collections.abc import Callable, MutableMapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.adapters.accounting.base import (
    CREATE_SENT,
    Posted,
    PostingExpense,
    PostingLine,
    PostingRefused,
)
from app.adapters.accounting.xero.bills import LINE_AMOUNT_TYPES
from app.adapters.accounting.xero.client import XeroApiError, XeroClient
from app.adapters.accounting.xero.contacts import create_contact, find_contact
from app.adapters.accounting.xero.documents import attach, is_gone, posting_error
from app.domain.enums import AmountType

SPEND = "SPEND"
FINAL = "AUTHORISED"  # the only status Xero takes for Spend Money
COLLECTION = "BankTransactions"
CENT = Decimal("0.01")
HUNDRED = Decimal(100)
NO_SUPPLIER = (
    "Xero takes no Spend Money without a supplier: type the supplier's name, then approve again"
)


def post_expense(
    xero: XeroClient,
    expense: PostingExpense,
    *,
    key: str,
    progress: MutableMapping[str, Any],
    save: Callable[[str], None],
) -> Posted:
    try:
        if "priced" not in progress:
            progress["bank_id"] = _bank_account(xero, expense)
            _refuse_unless_vat_matches(expense, _tax_rates(xero))
            progress["priced"] = True
            save("priced")
        if "contact_id" not in progress:
            progress["contact_id"], progress["contact_created"] = _contact(xero, expense, key)
            save("contact")
        if "checked" not in progress:
            _refuse_if_already_in_xero(xero, expense, progress["contact_id"])
            progress["checked"] = True
            save("checked")
        if "expense_id" not in progress:
            if CREATE_SENT not in progress:
                progress[CREATE_SENT] = True
                save(CREATE_SENT)
            body = _spend(expense, progress["contact_id"], progress["bank_id"])
            created = xero.put(COLLECTION, {COLLECTION: [body]}, key=key, unitdp=4)[COLLECTION][0]
            progress["expense_id"] = created["BankTransactionID"]
            progress["total"] = str(created["Total"])
            save("expense")
        resource = f"{COLLECTION}/{progress['expense_id']}"
        if "attached" not in progress:
            progress["attached"] = attach(xero, resource, expense.attachment)
            save("attached")
        if "noted" not in progress:
            xero.put(
                f"{resource}/History",
                {"HistoryRecords": [{"Details": _note(expense, progress)}]},
                key=f"{key}-note",
            )
            progress["noted"] = True
            save("noted")
    except XeroApiError as exc:
        raise posting_error(exc) from exc
    return Posted(
        external_id=progress["expense_id"],
        total=Decimal(progress["total"]) if progress.get("total") else None,
        contact_external_id=progress["contact_id"],
        contact_created=bool(progress["contact_created"]),
        attached=bool(progress["attached"]),
    )


def is_deleted(xero: XeroClient, expense_id: str) -> bool:
    return is_gone(xero, COLLECTION, expense_id)


def xero_vat(
    lines: Sequence[PostingLine], amount_type: AmountType, rates: dict[str, Decimal]
) -> Decimal:
    """The VAT Xero works out: line by line, each rounded to the fils. `rates`: TaxType ->
    percentage."""
    if amount_type is AmountType.NO_TAX:
        return Decimal(0)
    vat = Decimal(0)
    for line in lines:
        rate, amount = rates.get(line.tax_code, Decimal(0)), _amount(line)
        base = (
            amount * rate / (HUNDRED + rate)
            if amount_type is AmountType.INCLUSIVE
            else amount * rate / HUNDRED
        )
        vat += base.quantize(CENT, rounding=ROUND_HALF_UP)
    return vat


def _bank_account(xero: XeroClient, expense: PostingExpense) -> str:
    """The AccountID of the account it was paid from: `paid_through` is the bank account's code,
    or its AccountID when it has none (master data). Read fresh from Xero, never trusted from the
    last sync: it must still be active and in the expense's currency, because Xero would book a
    foreign amount on it without a word."""
    wanted = expense.paid_through
    banks = xero.get("Accounts", where='Type=="BANK"').get("Accounts", [])
    bank = next((a for a in banks if wanted in (a.get("Code"), a.get("AccountID"))), None)
    if bank is None or bank.get("Status") != "ACTIVE":
        raise PostingRefused(
            "the account it was paid through is no longer an active bank account in Xero: sync "
            "the master data and choose the account it was paid from"
        )
    currency = expense.currency or _base_currency(xero)
    if bank.get("CurrencyCode") and currency and bank["CurrencyCode"] != currency:
        raise PostingRefused(
            f"it is in {currency} but {bank.get('Name', 'the account')} is in "
            f"{bank['CurrencyCode']}: choose an account in {currency}, or correct the currency"
        )
    return str(bank["AccountID"])


def _base_currency(xero: XeroClient) -> str | None:
    organisation = (xero.get("Organisation").get("Organisations") or [{}])[0]
    currency = organisation.get("BaseCurrency")
    return currency if isinstance(currency, str) and currency else None


def _tax_rates(xero: XeroClient) -> dict[str, Decimal]:
    rates = xero.get("TaxRates").get("TaxRates") or []
    return {str(r["TaxType"]): Decimal(str(r.get("EffectiveRate") or 0)) for r in rates}


def _refuse_unless_vat_matches(expense: PostingExpense, rates: dict[str, Decimal]) -> None:
    vat = xero_vat(expense.lines, expense.amount_type, rates)
    if vat != expense.vat:
        raise PostingRefused(
            f"Xero would work out VAT {vat:.2f}, but the document says {expense.vat:.2f} (Xero "
            "rounds the VAT of each line): check the lines and their VAT, then approve again"
        )


def _contact(xero: XeroClient, expense: PostingExpense, key: str) -> tuple[str, bool]:
    """(ContactID, created now)."""
    name = (expense.supplier_name or "").strip()
    if expense.supplier_external_id:
        return expense.supplier_external_id, False
    if not name:
        raise PostingRefused(NO_SUPPLIER)
    found = find_contact(xero, name, expense.supplier_tax_number, None)
    if found:
        return found, False
    return create_contact(xero, name, expense.supplier_tax_number, key), True


def _refuse_if_already_in_xero(xero: XeroClient, expense: PostingExpense, contact_id: str) -> None:
    """This supplier's live Spend Money of the same date and total, or with the same printed
    number: typed by hand in Xero, or sent before. Our own retry never reaches here twice (the
    check is saved before the create, and the key returns our own)."""
    live = f'Type=="{SPEND}" AND Status!="DELETED" AND Contact.ContactID==guid("{contact_id}")'
    total = _total(expense)
    same_day = xero.get(COLLECTION, where=f"{live} AND Date=={_xero_date(expense.expense_date)}")
    if any(Decimal(str(t.get("Total"))) == total for t in same_day.get(COLLECTION, [])):
        raise PostingRefused(
            f"Spend Money of {total:.2f} on {expense.expense_date:%d %b %Y} from this supplier is "
            "already in Xero: reject this one as a duplicate, or correct it"
        )
    number = (expense.reference or "").strip()
    if number and '"' not in number:
        same_number = xero.get(COLLECTION, where=f'{live} AND Reference=="{number}"')
        if same_number.get(COLLECTION):
            raise PostingRefused(
                f"Spend Money with number {number} from this supplier is already in Xero: "
                "reject this one as a duplicate, or correct the number"
            )


def _spend(expense: PostingExpense, contact_id: str, bank_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Type": SPEND,
        "Status": FINAL,
        "Contact": {"ContactID": contact_id},
        "BankAccount": {"AccountID": bank_id},
        "Date": expense.expense_date.isoformat(),
        "LineAmountTypes": LINE_AMOUNT_TYPES[expense.amount_type],
        "LineItems": [
            {
                "Description": line.description,
                "Quantity": line.quantity,
                "UnitAmount": line.unit_amount,
                "AccountCode": line.account_code,
                "TaxType": line.tax_code,
            }
            for line in expense.lines
        ],
    }
    if expense.reference:
        body["Reference"] = expense.reference.strip()  # null, never invented
    return body


def _note(expense: PostingExpense, progress: MutableMapping[str, Any]) -> str:
    if expense.attachment and not progress["attached"]:
        return f"{expense.note} The original file could not be attached: it is in Missan's folders."
    return expense.note


def _total(expense: PostingExpense) -> Decimal:
    lines = sum((_amount(line) for line in expense.lines), Decimal(0))
    return lines + expense.vat if expense.amount_type is AmountType.EXCLUSIVE else lines


def _amount(line: PostingLine) -> Decimal:
    return (line.quantity * line.unit_amount).quantize(CENT, rounding=ROUND_HALF_UP)


def _xero_date(day: date) -> str:
    return f"DateTime({day.year},{day.month},{day.day})"
