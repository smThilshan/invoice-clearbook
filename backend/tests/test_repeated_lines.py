"""Decision 95: the table read twice. Amounts as the model read real invoices."""

from decimal import Decimal
from typing import Any

from app.domain.enums import AmountType
from app.domain.enums import DocumentType as T
from app.domain.invoice import Invoice, Line
from app.pipeline.repeated_lines import remove_repeated_lines, without_copy


def line(description: str, qty: str, price: str, amount: str) -> Line:
    return Line(description=description, qty=Decimal(qty), unit_price=Decimal(price),
                amount=Decimal(amount))  # fmt: skip


# Life Smoothies: three rows read by their description, then again by their item name.
LIFE = [
    line("Frozen Raspberry - Blueberry - Banana (Box 1 = 15 x 150g)", "1", "78.75", "78.75"),
    line("Frozen Mango - Pineapple - Passion Fruit (Box = 15 x 150g)", "1", "78.75", "78.75"),
    line("Apple Juice Life Juice Apple Juice (Box 12 x 1L) - UAE", "2", "42.00", "84.00"),
    line("Life Smoothies", "1", "78.75", "78.75"),
    line("Life Smoothies", "1", "78.75", "78.75"),
    line("Life Apple Juice - UAE", "2", "42.00", "84.00"),
]


def invoice(lines: list[Line], **changes: Any) -> Invoice:
    fields: dict[str, Any] = {"document_type": T.BILL, "subtotal": Decimal("241.50"),
                              "vat": Decimal("12.08"), "total": Decimal("253.58"),
                              "amount_type": AmountType.INCLUSIVE, "lines": lines}  # fmt: skip
    fields.update(changes)
    return Invoice(**fields)


def test_the_copy_goes_and_the_fuller_descriptions_stay() -> None:
    fixed = remove_repeated_lines(invoice(LIFE))

    assert fixed.lines == LIFE[:3]
    assert fixed.amount_type is AmountType.EXCLUSIVE  # 241.50 + 12.08 = 253.58, from the amounts


def test_the_fuller_half_is_kept_when_it_comes_second() -> None:
    assert remove_repeated_lines(invoice(LIFE[3:] + LIFE[:3])).lines == LIFE[:3]


def test_identical_rows_that_add_up_together_are_kept() -> None:
    """Two real cartons of the same thing: both halves together are the subtotal."""
    rows = [line("Milk", "1", "5.00", "5.00"), line("Bread", "1", "3.00", "3.00")] * 2
    real = invoice(rows, subtotal=Decimal("16.00"), vat=None, total=Decimal("16.00"))

    assert remove_repeated_lines(real) == real


def test_one_repeated_line_is_left_to_the_total_row_repair() -> None:
    rows = [line("Card Top-Up", "1", "20.00", "20.00"), line("Cash", "1", "20.00", "20.00")]
    nol = invoice(rows, subtotal=Decimal("20.00"), vat=None, total=Decimal("20.00"))

    assert remove_repeated_lines(nol) == nol


def test_zero_rows_are_no_copy() -> None:
    """RTA: "Trip 40, Discount 0, Total 40, Charity 0" is not a table read twice."""
    rows = [line("Trip Amount", "1", "40", "40"), line("Discount", "1", "0", "0"),
            line("Total Amount", "1", "40", "40"), line("Charity", "1", "0", "0")]  # fmt: skip
    rta = invoice(rows, subtotal=Decimal("40"), vat=None, total=Decimal("40"))

    assert remove_repeated_lines(rta) == rta


def test_nothing_changes_without_a_total_to_prove_it() -> None:
    unproven = invoice(LIFE, subtotal=None, total=None)

    assert remove_repeated_lines(unproven) == unproven


def test_rows_in_another_order_are_no_copy() -> None:
    shuffled = invoice([*LIFE[:3], LIFE[5], LIFE[3], LIFE[4]])

    assert remove_repeated_lines(shuffled) == shuffled


def test_without_copy_takes_either_total() -> None:
    included = LIFE  # lines with VAT included: one half is the total
    assert without_copy(included, (None, Decimal("241.50"))) == LIFE[:3]
    assert without_copy(included, (None, Decimal("483.00"))) is None
