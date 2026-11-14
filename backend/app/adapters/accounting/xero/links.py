"""Links into Xero's own screens (decision 70)."""

from urllib.parse import quote, urlencode

BILL_PAGE = "/AccountsPayable/View.aspx?InvoiceID={id}"
SPEND_PAGE = "/Bank/ViewTransaction.aspx?bankTransactionID={id}"


def bill_url(organisation_ref: str | None, bill_id: str) -> str:
    """The bill in Xero. With the organisation's ShortCode, Xero switches to that organisation
    first: Missan's staff are signed in to many."""
    return _url(organisation_ref, BILL_PAGE.format(id=quote(bill_id)))


def expense_url(organisation_ref: str | None, expense_id: str) -> str:
    """The Spend Money in Xero (decision 97)."""
    return _url(organisation_ref, SPEND_PAGE.format(id=quote(expense_id)))


def _url(organisation_ref: str | None, page: str) -> str:
    if not organisation_ref:
        return f"https://go.xero.com{page}"
    query = urlencode({"shortcode": organisation_ref, "redirecturl": page})
    return f"https://go.xero.com/organisationlogin/default.aspx?{query}"
