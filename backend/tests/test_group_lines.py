from decimal import Decimal

from app.adapters.accounting.base import PostingLine
from app.services.posting import MAX_DESCRIPTION, group_lines


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
