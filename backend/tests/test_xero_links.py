"""Links into Xero's screens (decision 70)."""

from urllib.parse import parse_qs, urlparse

from app.adapters.accounting.xero.links import bill_url

BILL = "0b5c375d-ab19-4131-bbf3-bf1bfbe6e021"


def test_a_bill_link_opens_the_right_organisation_first() -> None:
    link = urlparse(bill_url("!aB1cD", BILL))

    assert (link.netloc, link.path) == ("go.xero.com", "/organisationlogin/default.aspx")
    assert parse_qs(link.query) == {
        "shortcode": ["!aB1cD"],
        "redirecturl": [f"/AccountsPayable/View.aspx?InvoiceID={BILL}"],
    }


def test_without_a_short_code_the_link_opens_the_bill_in_the_signed_in_organisation() -> None:
    expected = "https://go.xero.com/AccountsPayable/View.aspx?InvoiceID=bill-1"
    assert bill_url(None, "bill-1") == expected
