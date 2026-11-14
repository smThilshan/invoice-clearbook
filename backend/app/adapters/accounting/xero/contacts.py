"""The supplier's Xero contact, for bills and expenses alike: found, else created once."""

import re
from typing import Any

from app.adapters.accounting.xero.client import XeroClient

TRN = re.compile(r"^\d{15}$")


def find_contact(
    xero: XeroClient, name: str, tax_number: str | None, external_id: str | None
) -> str | None:
    """The supplier's Xero ContactID: the matched contact, else by TRN, else by exact name."""
    if external_id:
        return external_id
    if tax_number and TRN.match(tax_number):
        same_trn = xero.get("Contacts", where=f'TaxNumber=="{tax_number}"')
        if same_trn.get("Contacts"):
            return str(same_trn["Contacts"][0]["ContactID"])
    wanted = name.strip().casefold()
    for contact in xero.get("Contacts", searchTerm=name).get("Contacts", []):
        if (contact.get("Name") or "").strip().casefold() == wanted:
            return str(contact["ContactID"])
    return None


def create_contact(xero: XeroClient, name: str, tax_number: str | None, key: str) -> str:
    new: dict[str, Any] = {"Name": name.strip()}
    if tax_number:
        new["TaxNumber"] = tax_number
    created = xero.put("Contacts", {"Contacts": [new]}, key=f"{key}-contact")
    return str(created["Contacts"][0]["ContactID"])
