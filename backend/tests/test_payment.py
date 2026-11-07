"""Decision 88: bill or expense, always chosen. Texts as our OCR read real slips."""

from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import DocumentType as T
from app.domain.invoice import Invoice
from app.pipeline.payment import Level, decide_payment, payment_issue
from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.issues import Severity

# M.H. Enterprises: an account supplier printing "CASH" for its van-sales customers. A bill.
MH = """TAX INVOICE Customer Original Invoice
Tax Reg. No 100213916800003
Bill To : FCC058 - CASH SALES - ARIVALAGAN
Tax Reg. : 100235614300003
Ship To : FCC2022331 - CASH SALES - CHA CHA CHAI CAFE L.L.C
Route Code CLDXB009
Tax Invoice No : CI-2610064951
Order No : CFDSO-2610056219
LPO No :
Payment Term : CASH
Salesman : ARIVALAGAN
INDIAN GREEN CARDAMOM 7 MM- 1 KG 280.00 5 14.00 294.00
Total Amount 280.00 14.00 294.00
Dely. Locn : MH DRY WAREHOUSE
Cash Invoice Delivery against Payment Only, Cash Payment against company receipts
Receiver's Signature & Company Seal Vehicle No."""

# Al Ain Farms: route sales, "PAYMENT DETAILS CASH:49.35" printed. A bill.
AL_AIN = """TAX INVOICE VAT REGISTRATION NO:100296342700003
INVOICE NO. 136726200800 (CASH)
CUSTOMER ID : 367013
NAME : ROAST PASTRIES COFFE SHOP FZ-LLC
TRN NUMBER : 100568308900003
ROUTE : 367-MIASEM AND ARJAN INVOICE DATE : 22/1/2026
TRIP ID : 712950 DELIVERY DATE : 22/1/2026
SALESMAN : (03-5209) MOHAMMED ASHIQ
FULL CREAM MILK 2L 5 8.00 40.00 2.00 42.00
NET INVOICE AMOUNT : 49.35
PAYMENT DETAILS
CASH:49.35 (AED)
SALESMAN REC. BY SIGN./STAMP"""

# Choithrams: route sales with "NET DUE THIS INVOICE". A bill.
CHOITHRAMS = """VAT REG.NO. : 100057393900003
INVOICE NO : SR0450050885 INVOICE TYPE : CASH
TAX INVOICE
NET DUE THIS INVOICE
SALES +1,520.00
NET SALES +1,512.00
PAYMENT DUE DATE : CASH ONLY"""

# Swades: a restaurant. An expense.
SWADES = """SWADES VEGETARIAN RESTAURANT
VAT TRN No: 100387351800003
TAX INVOICE
Invoice No W/17174/2025
Name : Cha Cha Chai Blue Tower
Bill Type : Cash
Samosa 10.00 2.00 20.00
Bill Total 40.00
Thank You For Ordering With us."""

# Saimun Rana: a handwritten invoice pad, nothing printed but the pad. An expense.
SAIMUN = """SAIMUN RANA BUILDING MATERIALS TRADING L.L.C.
No. 12951 TAX INVOICE TRN : 100248925800003
Mr. / M/s. Customer TRN Address
SILICON 1 5
Total 5
Receiver's Sign For SAIMUN RANA BUILDING MATERIALS TRADING L.L.C."""

# A Nol top-up paid by card at the machine. An expense.
NOL = """TVM Dubai 2020
Date: 2026/01/22 Time: 17:37:12
ACCEPTED
Merchant # :001119483436
Terminal # :36227905
Amount: 50,00"""

# Viva Supermarket's delivery app screen. An expense.
VIVA = """Viva Supermarket Delivered 22 January 2026
Pomace olive oil 3 L AED51.99
Total AED60.04
Paid with Visa 1310 AED 60.04
Delivery details"""

# Al Shahama: cash given and its change. An expense.
SHAHAMA = """TAX INVOICE
Total Amount : 4.28
Net Amount : 4.50
Tendered Amount 5.00
Balance Amount 0.50"""


def pages(text: str) -> list[PageText]:
    rows = text.splitlines()
    spans = [TextSpan(row, (0.0, n / 40, 1.0, n / 40 + 0.02), None) for n, row in enumerate(rows)]
    return [PageText(1, 1000, 1400, "ocr", spans)]


def invoice(**changes: Any) -> Invoice:
    fields: dict[str, Any] = {"document_type": T.BILL, "total": Decimal("4.50")}
    fields.update(changes)
    return Invoice(**fields)


@pytest.mark.parametrize(
    ("text", "vendor", "expected", "level", "why"),
    [
        (MH, "M.H. ENTERPRISES (L.L.C.)", T.BILL, Level.SURE, "account supplier"),
        (AL_AIN, "Al Ain Farms", T.BILL, Level.SURE, "account supplier"),
        (CHOITHRAMS, "T.CHOITHRAM & SONS L.L.C", T.BILL, Level.SURE, "credit or due terms"),
        (SWADES, "SWADES VEGETARIAN RESTAURANT", T.RECEIPT, Level.SURE, "shop"),
        (SAIMUN, "SAIMUN RANA BUILDING MATERIALS", T.RECEIPT, Level.WEAK, "no customer account"),
        (NOL, "TVM Dubai", T.RECEIPT, Level.PROVEN, '"ACCEPTED"'),
        (VIVA, "Viva Supermarket", T.RECEIPT, Level.PROVEN, '"Paid with"'),
        (SHAHAMA, "AL SHAHAMA", T.RECEIPT, Level.PROVEN, "cash given and change"),
    ],
)
def test_real_slips(text: str, vendor: str, expected: T, level: Level, why: str) -> None:
    decision = decide_payment(invoice(vendor_name=vendor), pages(text))

    assert decision is not None
    assert (decision.document_type, decision.level) == (expected, level)
    assert why in decision.reason


@pytest.mark.parametrize("model", [T.BILL, T.RECEIPT])
def test_the_models_type_does_not_decide(model: T) -> None:
    assert decide_payment(invoice(document_type=model), pages(SWADES)).document_type is T.RECEIPT  # type: ignore[union-attr]
    assert decide_payment(invoice(document_type=model), pages(MH)).document_type is T.BILL  # type: ignore[union-attr]


def test_cash_words_on_an_account_suppliers_invoice_are_no_proof() -> None:
    """MH: "Payment Term CASH", "CASH SALES", "Cash Payment against company receipts"."""
    decision = decide_payment(invoice(), pages(MH))

    assert decision is not None and decision.document_type is T.BILL
    assert "customer code" in decision.reason and "order number" in decision.reason


def test_money_given_equal_to_the_total_is_no_proof_on_an_account_suppliers_invoice() -> None:
    """Al Ain prints the total as cash given: proof only on a slip without a customer account."""
    exact = SHAHAMA.replace("5.00\nBalance Amount 0.50", "4.50\nBalance Amount 0.00")
    account = "CUSTOMER ID : 367013\nROUTE : 367-MIASEM\nSALESMAN : ASHIQ\n" + exact

    till = decide_payment(invoice(), pages(exact))
    van = decide_payment(invoice(), pages(account))

    assert till is not None and (till.document_type, till.level) == (T.RECEIPT, Level.PROVEN)
    assert van is not None and van.document_type is T.BILL


def test_a_payment_on_an_account_suppliers_invoice_stays_a_bill_but_is_asked() -> None:
    decision = decide_payment(invoice(), pages(MH + "\nPAID"))

    assert decision is not None and (decision.document_type, decision.level) == (T.BILL, Level.WEAK)


def test_credit_terms_make_a_bill() -> None:
    for terms in (
        "Payment Terms: 30 days",
        "Payment Term : Credit",
        "Net 30",
        "Due Date: 30/01/2026",
        "Due: 14/11/2026",
    ):
        decision = decide_payment(invoice(), pages(f"TAX INVOICE\n{terms}\nTotal 10.00"))
        assert decision is not None and decision.document_type is T.BILL, terms


@pytest.mark.parametrize("till", ["Change Due: 0.50", "Amount Due 10.00", "Balance Due 0.00"])
def test_a_tills_amount_due_is_not_a_due_date(till: str) -> None:
    decision = decide_payment(invoice(), pages(f"TAX INVOICE\nTotal 10.00\n{till}"))

    assert decision is not None and decision.document_type is T.RECEIPT


def test_a_credit_card_is_not_credit_terms() -> None:
    decision = decide_payment(invoice(), pages("TAX INVOICE\nPayment Type : Credit Card\nVISA"))

    assert decision is not None and decision.document_type is T.RECEIPT


def test_the_shop_is_read_from_the_suppliers_name_only() -> None:
    """MH's page names the buyer's café: not a café's slip."""
    one_clue = MH.replace("Route Code CLDXB009\n", "").replace("Order No : CFDSO-2610056219\n", "")
    without_account = "\n".join(
        row for row in one_clue.splitlines() if not row.startswith(("Bill To", "Ship To"))
    ).replace("Tax Reg. : 100235614300003\n", "")

    decision = decide_payment(invoice(vendor_name="M.H. ENTERPRISES"), pages(without_account))

    assert decision is not None and decision.reason != "shop, transport or restaurant"


def test_one_account_clue_alone_is_a_bill_not_sure() -> None:
    decision = decide_payment(invoice(), pages("TAX INVOICE\nSalesman : Ali\nTotal 10.00"))

    assert decision is not None and (decision.document_type, decision.level) == (T.BILL, Level.WEAK)


def test_a_receivers_sign_or_the_buyers_trn_alone_is_no_account() -> None:
    """Printed on invoice pads (Saimun) and full tax invoices from shops."""
    text = "TAX INVOICE TRN 100248925800003\nCustomer TRN 100568308900003\nReceiver's Sign"

    decision = decide_payment(invoice(), pages(text))

    assert decision is not None and decision.document_type is T.RECEIPT


def test_the_suppliers_memory_comes_first() -> None:
    decision = decide_payment(invoice(), pages(SWADES), remembered=T.BILL)

    assert decision is not None and (decision.document_type, decision.level) == (
        T.BILL,
        Level.PROVEN,
    )


@pytest.mark.parametrize("kind", [T.CREDIT_NOTE, T.NON_POSTABLE, T.SALES_INVOICE, T.DEBIT_NOTE])
def test_only_bills_and_expenses_are_decided(kind: T) -> None:
    assert decide_payment(invoice(document_type=kind), pages(VIVA), remembered=T.BILL) is None


@pytest.mark.parametrize(
    ("text", "vendor", "model", "asked"),
    [
        (VIVA, "Viva", T.BILL, False),  # proven: the model's bill is not asked about
        (MH, "M.H.", T.BILL, False),  # sure and the model agrees
        (MH, "M.H.", None, False),  # sure, the model's answer unreadable
        (MH, "M.H.", T.RECEIPT, True),  # sure, but the model read the other type
        (SAIMUN, "Saimun", T.RECEIPT, True),  # weak: always looked at
    ],
)
def test_a_warning_when_not_sure(text: str, vendor: str, model: T | None, asked: bool) -> None:
    decision = decide_payment(invoice(vendor_name=vendor), pages(text))
    assert decision is not None

    issues = payment_issue(decision, model)

    assert bool(issues) is asked
    if issues:
        [issue] = issues
        other = T.BILL if decision.document_type is T.RECEIPT else T.RECEIPT
        assert (issue.code, issue.severity, issue.suggestion) == (
            "bill_or_expense",
            Severity.WARNING,
            other.value,
        )
        assert decision.reason in issue.message
