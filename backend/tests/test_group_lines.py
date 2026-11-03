from decimal import Decimal

import pytest

from app.adapters.accounting.base import PostingLine
from app.domain.enums import AmountType
from app.services.posting import MAX_DESCRIPTION, combine_items, group_lines


def line(description: str | None, amount: str, account: str = "310", tax: str = "NONE",
         qty: str = "1") -> PostingLine:  # fmt: skip
    return PostingLine(description, Decimal(qty), Decimal(amount), account, tax)


def test_items_and_fees_on_one_account_become_one_line_with_the_exact_sum() -> None:
    """The Talabat order: eight items and two fees, all Cost of Goods Sold, no VAT."""
    talabat = [line("Eggs", "21.50"), line("Basil", "1.95", qty="3"), line("Banana", "10.95"),
               line("Avocado", "16.95"), line("Tea leaves", "17.85"), line("Bread buns", "3.99"),
               line("Pancake mix", "10.28"), line("Blueberries", "11.50"),
               line("Delivery fee", "0.00"), line("Service fee", "2.97")]  # fmt: skip

    [grouped] = group_lines(talabat)

    assert (grouped.quantity, grouped.unit_amount) == (Decimal(1), Decimal("101.84"))
    assert (grouped.account_code, grouped.tax_code) == ("310", "NONE")
    assert grouped.description is not None
    assert grouped.description.startswith("Eggs; Basil; Banana")
    assert grouped.description.endswith("Delivery fee; Service fee")


def test_one_line_per_account_and_vat_code_in_first_seen_order() -> None:
    mixed = [line("Rice", "100.00", tax="INPUT"), line("Soap", "20.00", account="408", tax="INPUT"),
             line("Oil", "50.00", tax="INPUT"), line("Bread", "5.00", tax="ZERO")]  # fmt: skip

    grouped = group_lines(mixed)

    assert [(g.account_code, g.tax_code, g.unit_amount) for g in grouped] == [
        ("310", "INPUT", Decimal("150.00")),
        ("408", "INPUT", Decimal("20.00")),  # a group of one is sent as it is
        ("310", "ZERO", Decimal("5.00")),
    ]
    assert grouped[1] == mixed[1]


def test_the_sum_never_changes_with_discounts_and_quantities() -> None:
    lines = [line("Milk", "4.25", qty="12", tax="INPUT"), line("Discount", "-5.00", tax="INPUT"),
             line("Cheese", "33.33", qty="3", tax="INPUT")]  # fmt: skip
    before = sum((x.quantity * x.unit_amount for x in lines), Decimal(0))

    [grouped] = group_lines(lines)

    assert grouped.unit_amount == before == Decimal("145.99")


def test_a_long_list_of_items_is_cut_to_the_description_limit() -> None:
    many = [line(f"Item number {n}", "1.00") for n in range(1000)]

    [grouped] = group_lines(many)

    assert grouped.description is not None and len(grouped.description) == MAX_DESCRIPTION
    assert grouped.description.endswith("…")


def test_lines_without_descriptions_still_group() -> None:
    [grouped] = group_lines([line(None, "1.00"), line(None, "2.00")])

    assert (grouped.description, grouped.unit_amount) == (None, Decimal("3.00"))


# --- item by item (decision 73) ------------------------------------------------------------

RATES = {"INPUT": Decimal("0.05"), "NONE": Decimal(0)}


def items(lines: list[PostingLine], amount_type: AmountType = AmountType.INCLUSIVE) -> list[str]:
    combined = combine_items(lines, RATES, amount_type)
    return [f"{x.description} {x.quantity} x {x.unit_amount}" for x in combined]


def test_identical_items_become_one_line_with_their_quantity_added() -> None:
    """The Marketplace bill: Garam Masala 1 x 8.80 three times, yogurt 1 x 6.95 twice."""
    masala, yogurt = (
        ("Bayara Whole Garam Masala", "8.80"),
        ("Almarai Full Fat Fresh Yogurt", "6.95"),
    )
    bill = [line(*masala, tax="INPUT")] * 3 + [line(*yogurt, tax="INPUT")] * 2

    assert items(bill) == [
        "Bayara Whole Garam Masala 3 x 8.80", "Almarai Full Fat Fresh Yogurt 2 x 6.95"]  # fmt: skip


def test_the_same_name_in_another_case_or_spacing_is_the_same_item() -> None:
    assert items([line("Milk  1L", "5.00", qty="2"), line("milk 1l", "5.00")]) == [
        "Milk  1L 3 x 5.00"
    ]


@pytest.mark.parametrize(
    ("other", "why"),
    [
        (line("Milk", "4.50"), "another price"),
        (line("Milk", "5.00", account="408"), "another account"),
        (line("Milk", "5.00", tax="INPUT"), "another VAT code"),
        (line("Cheese", "5.00"), "another item"),
    ],
)
def test_items_that_differ_in_anything_stay_apart(other: PostingLine, why: str) -> None:
    assert len(items([line("Milk", "5.00"), other])) == 2, why


def test_lines_without_a_name_are_never_combined() -> None:
    assert items([line(None, "5.00"), line(None, "5.00")]) == ["None 1 x 5.00", "None 1 x 5.00"]


def test_a_group_whose_vat_would_round_differently_is_sent_line_by_line() -> None:
    """VAT included in 0.31 is 0.01 a line, 0.03 for three; on one line of 0.93 it is 0.04, so
    the three stay apart. In 1.05 it is 0.05 a line and 0.10 on 2.10: combined."""
    assert items([line("Gum", "0.31", tax="INPUT")] * 3) == ["Gum 1 x 0.31"] * 3
    assert items([line("Gum", "1.05", tax="INPUT")] * 2) == ["Gum 2 x 1.05"]


def test_vat_added_on_top_is_checked_the_same_way() -> None:
    """5% of 0.30 is 0.015 -> 0.02 per line (0.04); 5% of 0.60 is 0.03: kept apart."""
    assert items([line("Clip", "0.30", tax="INPUT")] * 2, AmountType.EXCLUSIVE) == [
        "Clip 1 x 0.30", "Clip 1 x 0.30"]  # fmt: skip
