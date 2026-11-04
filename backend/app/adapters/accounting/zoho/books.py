"""The ConnectedSystem port for one Zoho Books organisation (decision 74).

Master data comes in; posting follows in the next change. Until then it says so in words for the
accountant, and a bill approved for a Zoho client goes back to review.
"""

from collections.abc import Callable, MutableMapping
from typing import Any

import httpx

from app.adapters.accounting.base import (
    AccountingSystemError,
    MasterData,
    Posted,
    PostingBill,
    PostingRefused,
)
from app.adapters.accounting.zoho.client import ZohoApiError, ZohoClient
from app.adapters.accounting.zoho.master_data import fetch_master_data
from app.domain.enums import AccountingProvider

NOT_YET = "is not available for Zoho Books yet"


class ZohoBooks:
    def __init__(
        self,
        access_token: str,
        organisation_id: str,
        region: str | None,
        http: httpx.Client | None = None,
    ) -> None:
        self._organisation_id = organisation_id
        self._zoho = ZohoClient(access_token, organisation_id, region, http)

    @property
    def provider(self) -> AccountingProvider:
        return AccountingProvider.ZOHO

    def fetch_master_data(self) -> MasterData:
        try:
            return fetch_master_data(self._zoho, self._organisation_id)
        except ZohoApiError as exc:
            raise AccountingSystemError(str(exc)) from exc

    def post_bill(
        self,
        bill: PostingBill,
        *,
        key: str,
        progress: MutableMapping[str, Any],
        save: Callable[[str], None],
    ) -> Posted:
        raise PostingRefused(f"sending bills {NOT_YET}")
