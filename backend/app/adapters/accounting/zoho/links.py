"""Links into Zoho Books' own screens (decision 70): the organisation and its data centre are in
the connection's organisation reference ("ae:905001"), saved by the master data sync."""

from urllib.parse import quote

from app.adapters.accounting.zoho import regions


def organisation_ref(region: str | None, organisation_id: str) -> str:
    return f"{region or regions.DEFAULT}:{organisation_id}"


def bill_url(reference: str | None, bill_id: str) -> str | None:
    """The bill in Zoho Books, in its organisation; None before the first sync."""
    region, _, organisation_id = (reference or "").partition(":")
    if region not in regions.REGIONS or not organisation_id:
        return None
    return f"https://books.zoho.{region}/app/{quote(organisation_id)}#/bills/{quote(bill_id)}"
