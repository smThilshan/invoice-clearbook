"""The ConnectedSystem port for one Zoho Books organisation (decision 74).

Connecting comes first; master data and posting follow in the next changes. Until then both say
so in words for the accountant, and a bill approved for a Zoho client goes back to review.
"""

from collections.abc import Callable, MutableMapping
from typing import Any

from app.adapters.accounting.base import (
    AccountingSystemError,
    MasterData,
    Posted,
    PostingBill,
    PostingRefused,
)
from app.domain.enums import AccountingProvider

NOT_YET = "is not available for Zoho Books yet"


class ZohoBooks:
    def __init__(self, access_token: str, organisation_id: str, region: str | None) -> None:
        self._access_token = access_token
        self._organisation_id = organisation_id
        self._region = region

    @property
    def provider(self) -> AccountingProvider:
        return AccountingProvider.ZOHO

    def fetch_master_data(self) -> MasterData:
        raise AccountingSystemError(f"bringing in master data {NOT_YET}")

    def post_bill(
        self,
        bill: PostingBill,
        *,
        key: str,
        progress: MutableMapping[str, Any],
        save: Callable[[str], None],
    ) -> Posted:
        raise PostingRefused(f"sending bills {NOT_YET}")
