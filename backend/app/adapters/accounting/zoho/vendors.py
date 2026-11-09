"""A document's supplier as a Zoho Books vendor (decision 74), for bills and expenses alike: the
matched one, else found by TRN or exact name, else created."""

import re
from typing import Any

from app.adapters.accounting.zoho.client import ZohoApiError, ZohoClient

TRN = re.compile(r"^\d{15}$")
NAME_TAKEN = 3062  # Zoho: a vendor with this name already exists


def find_vendor(
    zoho: ZohoClient, name: str, trn: str | None, external_id: str | None
) -> str | None:
    """The vendor's contact ID: the matched one, else by TRN, else by exact name. The vendor
    list carries no TRN, so a TRN match is confirmed on each candidate's own record."""
    if external_id:
        return external_id
    if trn and TRN.match(trn):
        found = zoho.get("contacts", contact_type="vendor", search_text=trn).get("contacts", [])
        for candidate in found[:5]:
            contact = zoho.get(f"contacts/{candidate['contact_id']}").get("contact", {})
            if contact.get("tax_reg_no") == trn:
                return str(candidate["contact_id"])
    return _by_name(zoho, name)


def create_vendor(zoho: ZohoClient, name: str, trn: str | None) -> str:
    registered = bool(trn and TRN.match(trn))
    new: dict[str, Any] = {
        "contact_name": name.strip(),
        "contact_type": "vendor",
        "tax_treatment": "vat_registered" if registered else "vat_not_registered",
    }
    if registered:
        new["tax_reg_no"] = trn
    try:
        return str(zoho.post("contacts", new)["contact"]["contact_id"])
    except ZohoApiError as exc:
        # The name is taken by a vendor the search did not return (spelling, spaces): use it.
        found = _by_name(zoho, name) if exc.code == NAME_TAKEN else None
        if found is None:
            raise
        return found


def _by_name(zoho: ZohoClient, name: str) -> str | None:
    wanted = name.strip().casefold()
    found = zoho.get("contacts", contact_type="vendor", contact_name=name.strip())
    for contact in found.get("contacts", []):
        if (contact.get("contact_name") or "").strip().casefold() == wanted:
            return str(contact["contact_id"])
    return None
