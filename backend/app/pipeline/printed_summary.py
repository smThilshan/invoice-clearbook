"""The printed VAT summary (decision 56): receipts print, after the items, the amount before VAT,
the VAT and the total (a UAE tax invoice must). The model often copies those summary rows, or the
cash paid, into the item lines ("VAT 5% 5.30", "CASH 115.00", "Net Amount 11.00"), repeats an
item whose amount the receipt prints twice, or leaves the total empty. Right after extraction,
when the lines do not add up and the page proves the answer, the summary lines go and the
subtotal, VAT and total are taken from the page; the VAT type is then set from the amounts.

Proven means, all of these (otherwise nothing changes and the checks flag it as before):
- Three amounts printed on the page with before VAT + VAT = total exactly and VAT = 5% of the
  amount before VAT (within rounding). Found by their numbers only: OCR garbles the labels
  ("Bi ount: 111.28"), not the numbers.
- They agree with the model exactly: on the total it read, or on its VAT when it read no total.
- Only lines at the end are removed (the summary is printed after the items), and each one is
  the subtotal, the VAT or the total, or a payment above the total whose change is printed.
- The lines left add up exactly to the total (VAT included in the prices) or to the amount
  before VAT, and there is exactly one such answer.

Cash rounding (decision 58): a shop rounds what is paid ("Rounding 0.02", "Net Amount 3.50").
Then before VAT + VAT + the rounding = the total, all four printed, the rounding at most 0.24,
and the items include VAT (before VAT + VAT): with VAT on top, such a gap is a discount.
The rounding becomes its own line, "Rounding", with no VAT (it is not a supply); its account
follows the goods (coding). The subtotal is then the total less the VAT, as for any charge.

No supplier names, no label words. The model's own answer stays in `extractions.raw_output`.
"""

import re
from collections.abc import Sequence
from decimal import Decimal

from app.domain.enums import TaxType
from app.domain.invoice import Invoice, Line
from app.pipeline.read import PageText
from app.pipeline.verify.normalize import fold_ocr_digits, to_ascii_digits
from app.pipeline.verify.vat_type import vat_type_from_amounts

CENT = Decimal("0.01")
UAE_VAT = Decimal("0.05")
RATE_SLACK = Decimal("0.02")  # VAT worked out per line rounds a little differently
MAX_REMOVED = 4  # summary rows a receipt prints: amount before VAT, VAT, total, paid
MAX_ROUNDING = Decimal("0.24")  # cash is rounded to the nearest 0.25 at most
ROUNDING = "Rounding"

# Money as printed with two decimals ("1,050.10", "115.00"); not phone numbers or barcodes.
_MONEY = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+\.\d{2}(?!\d)|(?<![\d.,])\d+\.\d{2}(?!\d)")

_Summary = tuple[Decimal, Decimal, Decimal, Decimal]  # before VAT, VAT, total, rounding


def apply_printed_summary(invoice: Invoice, pages: Sequence[PageText]) -> Invoice:
    if not invoice.lines or _adds_up(invoice):
        return invoice
    printed = _printed_money(pages)
    answers = {
        answer for summary in _agreeing(_summaries(printed), invoice)
        if (answer := _lines_to_remove(invoice, summary, printed)) is not None
    }  # fmt: skip
    if len(answers) != 1:
        return invoice  # nothing proven, or two answers: never guess
    (_, vat, total, rounding), removed = answers.pop()
    lines = invoice.lines[: len(invoice.lines) - removed]
    if rounding:
        lines = [*lines, Line(description=ROUNDING, amount=rounding, tax_type=TaxType.NONE)]
    fixed = invoice.model_copy(update={
        "lines": lines, "subtotal": total - vat, "vat": vat, "total": total,
    })  # fmt: skip
    return fixed.model_copy(update={"amount_type": vat_type_from_amounts(fixed)})


def _adds_up(invoice: Invoice) -> bool:
    if invoice.total is None:
        return False
    lines = sum((line.amount for line in invoice.lines), Decimal(0))
    vat = invoice.vat or Decimal(0)
    return abs(lines - invoice.total) <= CENT or abs(lines + vat - invoice.total) <= CENT


def _printed_money(pages: Sequence[PageText]) -> set[Decimal]:
    exact = "\n".join(page.text for page in pages)
    ocr = "\n".join(fold_ocr_digits(page.text) for page in pages if page.source == "ocr")
    found = _MONEY.findall(to_ascii_digits(f"{exact}\n{ocr}"))
    return {amount for text in found if (amount := Decimal(text.replace(",", ""))) > 0}


def _summaries(printed: set[Decimal]) -> list[_Summary]:
    roundings = [Decimal(0), *(r for r in printed if r <= MAX_ROUNDING)]
    return [
        (before_vat, vat, before_vat + vat + rounding, rounding)
        for vat in printed
        for before_vat in printed
        if before_vat > vat and abs(before_vat * UAE_VAT - vat) <= RATE_SLACK
        for rounding in (*roundings, *(-r for r in roundings if r))
        if abs(rounding) != vat and before_vat + vat + rounding in printed
    ]


def _agreeing(summaries: list[_Summary], invoice: Invoice) -> list[_Summary]:
    """The model read the total (or, without one, the VAT) right: the summary must match it.
    Without a total there is nothing to tell a rounding by: only summaries without one."""
    if invoice.total is not None:
        return [s for s in summaries if s[2] == invoice.total]
    return [s for s in summaries if invoice.vat is not None and s[1] == invoice.vat and not s[3]]


def _lines_to_remove(
    invoice: Invoice, summary: _Summary, printed: set[Decimal]
) -> tuple[_Summary, int] | None:
    """How many lines at the end to remove so the rest add up to the summary; None if none.
    The rest are the items: before VAT + VAT (VAT included in the prices) or before VAT."""
    before_vat, vat, total, rounding = summary
    items = before_vat + vat
    rows = {before_vat, vat, items, total, abs(rounding)}
    slack = Decimal(0) if rounding else CENT  # the rounding itself is about a cent
    amounts = [line.amount for line in invoice.lines]

    def summary_row(amount: Decimal) -> bool:
        return abs(amount) in rows or (amount > total and amount - total in printed)

    for removed in range(min(MAX_REMOVED, len(amounts) - 1) + 1):  # never every line
        if removed and not summary_row(amounts[-removed]):
            return None  # a real item: nothing before it is a summary row either
        rest = sum(amounts[: len(amounts) - removed], Decimal(0))
        # a rounding only with prices including VAT (a shop's cash rounding): with VAT added on
        # top, a gap that size is a discount, which changes the VAT
        if abs(rest - items) <= slack or (not rounding and abs(rest - before_vat) <= slack):
            return summary, removed
    return None
