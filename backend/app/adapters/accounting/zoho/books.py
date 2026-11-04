"""The ConnectedSystem port for one Zoho Books organisation (decision 74): master data in
(`master_data.py`), bills out (`bills.py`)."""

from collections.abc import Callable, MutableMapping
from typing import Any

import httpx

from app.adapters.accounting.base import (
    AccountingSystemError,
    MasterData,
    Posted,
    PostingBill,
)
from app.adapters.accounting.zoho import bills, links
from app.adapters.accounting.zoho.client import ZohoApiError, ZohoClient
from app.adapters.accounting.zoho.master_data import fetch_master_data
from app.domain.enums import AccountingProvider


class ZohoBooks:
    def __init__(
        self,
        access_token: str,
        organisation_id: str,
        region: str | None,
        http: httpx.Client | None = None,
    ) -> None:
        self._reference = links.organisation_ref(region, organisation_id)
        self._zoho = ZohoClient(access_token, organisation_id, region, http)

    @property
    def provider(self) -> AccountingProvider:
        return AccountingProvider.ZOHO

    def fetch_master_data(self) -> MasterData:
        try:
            return fetch_master_data(self._zoho, self._reference)
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
        return bills.post_bill(self._zoho, bill, key=key, progress=progress, save=save)

    def is_deleted(self, external_id: str) -> bool:
        return bills.is_deleted(self._zoho, external_id)
