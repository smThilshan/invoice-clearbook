"""The accounting systems Invoice Clearbook can connect to (decision 43): one entry each.

Everything provider-specific lives in its folder under adapters/accounting/; the services, the
worker and the API find a client's provider here and never name it. Adding Zoho = a `zoho/`
folder with a Connector and a ConnectedSystem, and one entry below.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.adapters.accounting.base import ConnectedSystem, Connector
from app.adapters.accounting.xero.auth import XeroAuth
from app.adapters.accounting.xero.master_data import XeroAccountingSystem
from app.core.config import Settings
from app.domain.enums import AccountingProvider


@dataclass(frozen=True, slots=True)
class ProviderEntry:
    provider: AccountingProvider
    name: str  # shown on screen: "Xero"
    slug: str  # in web addresses: /xero/connect, /xero/callback (registered with the provider)
    bills_where: str  # where the accountant finds sent bills in the provider's own screens
    connector: Callable[[Settings], Connector]
    system: Callable[[str, str], ConnectedSystem]  # (access token, organisation id) -> adapter


Registry = Mapping[AccountingProvider, ProviderEntry]

PROVIDERS: Registry = {
    AccountingProvider.XERO: ProviderEntry(
        provider=AccountingProvider.XERO,
        name="Xero",
        slug="xero",
        bills_where="Bills to pay, Awaiting approval",
        connector=XeroAuth,
        system=XeroAccountingSystem,
    ),
}


def by_slug(registry: Registry, slug: str) -> ProviderEntry | None:
    return next((entry for entry in registry.values() if entry.slug == slug), None)
