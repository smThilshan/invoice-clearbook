"""TRNs read next to their labels (decision 29). Layouts from real UAE invoices; numbers made up."""

from typing import Literal

import pytest

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.grounding import check_grounding
from app.pipeline.verify.issues import Severity
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
