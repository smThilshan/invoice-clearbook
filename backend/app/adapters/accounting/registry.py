"""The accounting systems Invoice Clearbook can connect to (decision 43): one entry each.

Everything provider-specific lives in its folder under adapters/accounting/; the services, the
worker and the API find a client's provider here and never name it. A provider = its folder with
a Connector and a ConnectedSystem, and one entry below.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.adapters.accounting.base import ConnectedSystem, Connector
from app.adapters.accounting.xero.auth import XeroAuth
from app.adapters.accounting.xero.links import bill_url
from app.adapters.accounting.xero.master_data import XeroAccountingSystem
from app.adapters.accounting.zoho.auth import ZohoAuth
from app.adapters.accounting.zoho.books import ZohoBooks
from app.adapters.accounting.zoho.links import bill_url as zoho_bill_url
from app.core.config import Settings
from app.domain.enums import AccountingProvider


@dataclass(frozen=True, slots=True)
class ProviderEntry:
    provider: AccountingProvider
    name: str  # shown on screen: "Xero"
    slug: str  # in web addresses: /xero/connect, /xero/callback (registered with the provider)
    bills_where: str  # where the accountant finds sent bills in the provider's own screens
    connector: Callable[[Settings], Connector]
    # (access token, organisation id, region) -> adapter; region: the connection's data centre
    system: Callable[[str, str, str | None], ConnectedSystem]
    # (organisation ref, external id) -> the posted document in the provider's screens (dec. 70)
    document_url: Callable[[str | None, str], str | None] = lambda _ref, _id: None
    # Every bill must carry a number (Zoho): a receipt printed without one is an error in review,
    # so the accountant types it before approving (option B, decision 74).
    number_required: bool = False


Registry = Mapping[AccountingProvider, ProviderEntry]

PROVIDERS: Registry = {
    AccountingProvider.XERO: ProviderEntry(
        provider=AccountingProvider.XERO,
        name="Xero",
        slug="xero",
        bills_where="Bills to pay, Awaiting approval",
        connector=XeroAuth,
        system=lambda token, organisation, _region: XeroAccountingSystem(token, organisation),
        document_url=bill_url,
    ),
    AccountingProvider.ZOHO: ProviderEntry(
        provider=AccountingProvider.ZOHO,
        name="Zoho Books",
        slug="zoho",
        bills_where="Purchases, Bills, Pending Approval",
        connector=ZohoAuth,
        system=ZohoBooks,
        document_url=zoho_bill_url,
        number_required=True,
    ),
}


def by_slug(registry: Registry, slug: str) -> ProviderEntry | None:
    return next((entry for entry in registry.values() if entry.slug == slug), None)
