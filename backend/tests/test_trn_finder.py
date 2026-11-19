"""TRNs read next to their labels (decision 29). Layouts from real UAE invoices; numbers made up."""

from typing import Literal

import pytest

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.grounding import check_grounding
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.trn_finder import PrintedTrns, find_printed_trns

SUPPLIER = "100111222333003"
CUSTOMER = "100444555666003"


def page(
    *lines: tuple[float, float, str], source: Literal["text_layer", "ocr"] = "ocr"
) -> PageText:
    """Lines as (y, x, text), each word its own span, like a PDF text layer."""
    spans = []
    for y, x, text in lines:
        for n, word in enumerate(text.split()):
            left = x + n * 0.06
            spans.append(TextSpan(word, (left, y, left + 0.05, y + 0.01), None))
    return PageText(1, 1000, 1400, source, spans)


def found(
    *lines: tuple[float, float, str], source: Literal["text_layer", "ocr"] = "ocr"
) -> PrintedTrns:
    return find_printed_trns([page(*lines, source=source)])


def test_header_vat_registration_is_the_supplier_and_lower_trn_the_customer() -> None:
    assert found(
        (0.12, 0.42, "DAIRY DIVISION"),
        (0.13, 0.43, "TAX INVOICE"),
        (0.14, 0.17, f"VAT REGISTRATION NO:{SUPPLIER}   EXCISE NO:1000523074"),
        (0.28, 0.07, "Name : Cafe Example FZ-LLC"),
        (0.29, 0.07, f"TRN NUMBER : {CUSTOMER}"),
    ) == PrintedTrns(SUPPLIER, CUSTOMER)


def test_scanner_misreads_are_corrected_on_ocr_text_only() -> None:
    lines = (
        (0.14, 0.17, f"VAT REGISTRALION NO:{SUPPLIER}"),  # scanner: REGISTRATION misread
        (0.29, 0.07, "TRN NUNBER : 10044455S666OO3"),  # scanner: 5 read as S, 0 as O
    )

    assert found(*lines, source="ocr") == PrintedTrns(SUPPLIER, CUSTOMER)
    assert found(*lines, source="text_layer").customer is None  # exact text: no guessing


def test_excise_licence_and_phone_numbers_are_never_trns() -> None:
    assert found(
        (0.14, 0.17, "EXCISE NO:100052307412345"),
        (0.15, 0.17, "Licence No 100052307412345"),
        (0.16, 0.17, "Tel 100052307412345"),
    ) == PrintedTrns(None, None)


def test_trn_is_exactly_15_digits() -> None:
    assert found((0.14, 0.17, "TRN: 10011122233300")).supplier is None  # 14
    assert found((0.14, 0.17, "TRN: 1001112223330031")).supplier is None  # 16


def test_unreadable_header_trn_is_unknown_not_replaced_by_the_customer_one() -> None:
    result = found(
        (0.14, 0.17, "VAT REGISTRATION NO 10011122233300? EXCISE"),  # one digit unreadable
        (0.29, 0.07, f"TRN NUMBER :{CUSTOMER}"),
    )

    assert result == PrintedTrns(None, CUSTOMER)


def test_trn_under_a_customer_label_is_the_customer_one() -> None:
    result = found(
        (0.30, 0.07, "Bill To: Cafe Example"),
        (0.32, 0.07, f"TRN: {CUSTOMER}"),
    )

    assert result == PrintedTrns(None, CUSTOMER)


@pytest.mark.parametrize("copy", ["CUSTOMER COPY", "Customer Copy", "CUSTOMER CORY"])
def test_customer_copy_on_a_shop_slip_is_not_a_customer_block(copy: str) -> None:
    """Al Shahama: "CUSTOMER COPY" (OCR: "CORY") right above the shop's own TRN."""
    result = found(
        (0.10, 0.20, "AL SHAHAMA SUPERMARKET"),
        (0.14, 0.30, "TAX INVOICE"),
        (0.15, 0.30, copy),
        (0.16, 0.30, f"TRN: {SUPPLIER}"),
    )

    assert result == PrintedTrns(SUPPLIER, None)


def test_trn_printed_in_groups_is_read_whole() -> None:
    assert found((0.1, 0.1, "Tax Registration Number: 100 1112 2233 3003")).supplier == SUPPLIER
    assert found((0.1, 0.1, "TRN 100-1112-2233-3003")).supplier == SUPPLIER


def test_same_trn_printed_twice_counts_once() -> None:
    result = found((0.1, 0.1, f"TRN {SUPPLIER}"), (0.9, 0.1, f"TRN {SUPPLIER}"))

    assert result == PrintedTrns(SUPPLIER, None)


# --- grounding against the printed TRNs ---------------------------------------------------

HEADER = (
    (0.14, 0.17, f"VAT REGISTRATION NO:{SUPPLIER}"),
    (0.29, 0.07, f"TRN NUMBER : {CUSTOMER}"),
)


def trn_issues(
    invoice: Invoice, verified: frozenset[str] = frozenset()
) -> list[tuple[str, str | None, str | None, Severity]]:
    return [
        (i.code, i.field, i.suggestion, i.severity)
        for i in check_grounding(invoice, [page(*HEADER)], verified=verified)
        if i.field in ("vendor_trn", "recipient_trn")
    ]


def test_customer_trn_given_as_supplier_trn_is_an_error_with_the_printed_one() -> None:
    invoice = Invoice(document_type=DocumentType.BILL, vendor_trn=CUSTOMER, recipient_trn=CUSTOMER)

    assert trn_issues(invoice) == [
        ("trn_differs_from_printed", "vendor_trn", SUPPLIER, Severity.ERROR)
    ]


def test_matching_trns_have_no_issue() -> None:
    invoice = Invoice(document_type=DocumentType.BILL, vendor_trn="100-1112-2233-3003",
                      recipient_trn=CUSTOMER)  # fmt: skip

    assert trn_issues(invoice) == []


def test_printed_but_missed_trns_are_reported() -> None:
    assert trn_issues(Invoice(document_type=DocumentType.BILL)) == [
        ("trn_not_extracted", "vendor_trn", SUPPLIER, Severity.ERROR),
        ("trn_not_extracted", "recipient_trn", CUSTOMER, Severity.WARNING),
    ]


def test_trn_confirmed_by_the_accountant_is_not_questioned() -> None:
    invoice = Invoice(document_type=DocumentType.BILL, vendor_trn="100999888777003",
                      recipient_trn=CUSTOMER)  # fmt: skip

    assert trn_issues(invoice, frozenset({"vendor_trn"})) == []


# --- decision 92: the client's own TRNs settle whose TRN is whose -------------------------


def test_without_own_trns_nothing_changes() -> None:
    lines = ((0.14, 0.17, f"VAT REGISTRATION NO:{SUPPLIER}"), (0.29, 0.07, f"TRN : {CUSTOMER}"))

    assert find_printed_trns([page(*lines)], own=()) == find_printed_trns([page(*lines)])


def test_own_trn_printed_as_vat_no_is_the_customers_not_the_suppliers() -> None:
    """Luma Trading: the supplier's header TRN unreadable on the photo, the customer's printed
    as "VAT No." on the right. Without the own TRN it was taken for the supplier's."""
    lines = (
        (0.20, 0.17, "- '955900003"),  # the supplier's TRN, cut off by the photo
        (0.36, 0.67, f"VAT No. : {CUSTOMER}"),
    )

    assert found(*lines) == PrintedTrns(CUSTOMER, None)  # before: the customer's as supplier's
    assert find_printed_trns([page(*lines)], own={CUSTOMER}) == PrintedTrns(None, CUSTOMER)


def test_a_single_other_trn_is_the_suppliers_even_on_a_merged_ship_to_line() -> None:
    """Cake Walk: OCR merged "Ship to:" (left) with the supplier's "VAT number" (right)."""
    lines = (
        (0.19, 0.10, f"Ship to: VAT number {SUPPLIER}"),
        (0.24, 0.10, "TRN Number."),  # the customer's, left empty
    )

    assert found(*lines) == PrintedTrns(None, None)  # before: in the customer block
    assert find_printed_trns([page(*lines)], own={CUSTOMER}) == PrintedTrns(SUPPLIER, None)


def test_an_unreadable_label_above_keeps_the_supplier_unknown() -> None:
    lines = (
        (0.10, 0.17, "TRN : 1001112223"),  # the header's TRN, unreadable: maybe the supplier's
        (0.30, 0.07, f"Bill to TRN : {SUPPLIER}"),
    )

    assert find_printed_trns([page(*lines)], own={CUSTOMER}).supplier is None


def test_with_several_other_trns_the_positions_decide() -> None:
    other = "100777888999003"
    lines = (
        (0.14, 0.17, f"VAT REGISTRATION NO:{SUPPLIER}"),
        (0.29, 0.07, f"Bill to TRN : {other}"),
        (0.31, 0.07, f"TRN : {CUSTOMER}"),
    )

    assert find_printed_trns([page(*lines)], own={CUSTOMER}) == PrintedTrns(SUPPLIER, CUSTOMER)


def test_supplier_trn_misread_gets_the_printed_one_as_one_click() -> None:
    lines = ((0.19, 0.10, f"Ship to: VAT number {SUPPLIER}"), (0.24, 0.10, "TRN Number."))
    invoice = Invoice(document_type=DocumentType.BILL, vendor_trn="cw-381519")

    issues = check_grounding(invoice, [page(*lines)], own_trns={CUSTOMER})

    assert [(i.code, i.field, i.suggestion, i.severity) for i in issues
            if i.field == "vendor_trn"] == [
        ("trn_differs_from_printed", "vendor_trn", SUPPLIER, Severity.ERROR)
    ]  # fmt: skip


# --- decision 96: one character unclear, and whose TRN when the finder missed a label -------

# CRS/17933: the supplier's "TRN" read as "[RN", and a 7 of its number as "/".
SUPPLIER_UNCLEAR = f"{SUPPLIER[:8]}/{SUPPLIER[9:]}"
CRS_HEADER = (0.16, 0.43, f"[RN : {SUPPLIER_UNCLEAR}")


def vendor_trn_issues(
    lines: tuple[tuple[float, float, str], ...],
    source: Literal["text_layer", "ocr"] = "ocr",
    known: tuple[str, ...] = (),
    **trns: str,
) -> list[Issue]:
    invoice = Invoice(document_type=DocumentType.BILL, **trns)
    return [i for i in check_grounding(invoice, [page(*lines, source=source)], known_trns=known)
            if i.field == "vendor_trn"]  # fmt: skip


def test_a_supplier_trn_with_one_unclear_character_is_a_warning_quoting_it() -> None:
    [issue] = vendor_trn_issues((CRS_HEADER,), vendor_trn=SUPPLIER)

    assert (issue.code, issue.severity) == ("not_on_document", Severity.WARNING)
    assert SUPPLIER_UNCLEAR in issue.message


@pytest.mark.parametrize(
    "printed",
    [
        f"{SUPPLIER[:8]}/{SUPPLIER[9:-1]}",  # one character unclear and one missing
        f"{SUPPLIER[:8]}//{SUPPLIER[10:]}",  # two unclear
        SUPPLIER.replace("1", "7", 2),  # two different digits
        SUPPLIER.replace("2", "8", 1),  # one different digit: another TRN
    ],
)
def test_more_than_one_character_off_stays_an_error(printed: str) -> None:
    [issue] = vendor_trn_issues(((0.16, 0.43, f"TRM : {printed}"),), vendor_trn=SUPPLIER)

    assert issue.severity is Severity.ERROR


# Al Ain Farms, live 2026-11-19: OCR skipped "VAT REGISTRATION NO:100296342700003" (small,
# beside the excise number) but read the customer's TRN. The TRN is printed: never say it is
# not on the document (decision 104). Made-up values in the same layout.
SKIPPED_TRN_LINE = (
    (0.05, 0.10, "TAX INVOICE"),
    (0.07, 0.30, "EXCISE NO:1000523074"),
    (0.09, 0.20, "INV0ICE NO.136726210815"),
    (0.14, 0.05, "CUSTOMER ID"),
    (0.14, 0.30, "367013"),
    (0.16, 0.05, "NAME"),
    (0.16, 0.30, "MADE-UP COFFEE SHOP LLC"),
    (0.18, 0.05, "ADDRESS"),
    (0.18, 0.30, "SCIENCE PARK, DUBAI"),
    (0.20, 0.05, "TRN NUMBER"),
    (0.20, 0.30, CUSTOMER),
)


def test_a_known_supplier_trn_whose_line_the_scanner_skipped_is_a_warning_saying_so() -> None:
    [issue] = vendor_trn_issues(SKIPPED_TRN_LINE, known=(SUPPLIER,), vendor_trn=SUPPLIER)

    assert issue.severity is Severity.WARNING
    assert issue.message == (
        "Our scanner could not read the supplier VAT registration no on this photo, so "
        f"{SUPPLIER} was not checked automatically. It is the TRN on this supplier's approved "
        "documents. Please confirm it matches the document."
    )


def test_an_unknown_supplier_trn_whose_line_was_skipped_must_be_checked() -> None:
    """The model misreads a digit (bynur agro: two TRNs for one supplier): a TRN nobody has
    confirmed blocks approval until the accountant ticks it, said honestly."""
    [issue] = vendor_trn_issues(SKIPPED_TRN_LINE, known=(CUSTOMER,), vendor_trn=SUPPLIER)

    assert issue.severity is Severity.ERROR
    assert issue.message.startswith(
        f"Our scanner did not read the supplier VAT registration no {SUPPLIER} on this photo"
    )
    assert "tick 'Checked against the original'" in issue.message
    assert "not found on the document" not in issue.message


def test_a_supplier_trn_missing_from_a_digital_pdf_stays_an_error() -> None:
    [issue] = vendor_trn_issues(((0.05, 0.10, "TAX INVOICE"),), source="text_layer",
                                vendor_trn=SUPPLIER)  # fmt: skip

    assert issue.severity is Severity.ERROR
    assert "was not found on the document" in issue.message


def test_one_unclear_character_is_no_excuse_on_a_digital_pdf() -> None:
    [issue] = vendor_trn_issues((CRS_HEADER,), source="text_layer", vendor_trn=SUPPLIER)

    assert issue.severity is Severity.ERROR


def test_the_buyers_trn_with_one_unclear_character_is_not_the_suppliers() -> None:
    """KSGN: the model took the customer's TRN, printed (one character unclear) under "Buyer"."""
    lines = ((0.22, 0.06, "Buyer"), (0.29, 0.10, f"{CUSTOMER[:10]}c{CUSTOMER[11:]}"))

    [issue] = vendor_trn_issues(lines, vendor_trn=CUSTOMER)

    assert (issue.code, issue.severity) == ("not_on_document", Severity.ERROR)


def test_the_customers_trn_is_not_offered_when_the_suppliers_label_was_misread() -> None:
    """CRS/17933: the finder missed "[RN" and took the customer's TRN below for the supplier's;
    the model read both. Neither is suggested: a warning asks which is whose."""
    lines = (CRS_HEADER, (0.18, 0.06, "M/S ROAST PASTRIES"), (0.22, 0.05, f"TRN : {CUSTOMER}"))

    [issue] = vendor_trn_issues(lines, vendor_trn=SUPPLIER, recipient_trn=CUSTOMER)

    assert (issue.code, issue.severity, issue.suggestion) == ("trn_whose", Severity.WARNING,
                                                              None)  # fmt: skip


def test_a_model_that_swapped_the_trns_is_still_corrected() -> None:
    """The supplier's TRN on top, the customer's below: the model read them the other way."""
    lines = ((0.14, 0.17, f"VAT REGISTRATION NO:{SUPPLIER}"), (0.30, 0.07, f"TRN : {CUSTOMER}"))

    [issue] = vendor_trn_issues(lines, vendor_trn=CUSTOMER, recipient_trn=SUPPLIER)

    assert (issue.code, issue.severity, issue.suggestion) == ("trn_differs_from_printed",
                                                              Severity.ERROR, SUPPLIER)  # fmt: skip
