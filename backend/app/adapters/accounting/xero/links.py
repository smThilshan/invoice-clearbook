"""Links into Xero's own screens (decision 70)."""

from urllib.parse import quote, urlencode

BILL_PAGE = "/AccountsPayable/View.aspx?InvoiceID={id}"


def bill_url(organisation_ref: str | None, bill_id: str) -> str:
    """The bill in Xero. With the organisation's ShortCode, Xero switches to that organisation
    first: Missan's staff are signed in to many."""
    page = BILL_PAGE.format(id=quote(bill_id))
    if not organisation_ref:
        return f"https://go.xero.com{page}"
    query = urlencode({"shortcode": organisation_ref, "redirecturl": page})
    return f"https://go.xero.com/organisationlogin/default.aspx?{query}"
