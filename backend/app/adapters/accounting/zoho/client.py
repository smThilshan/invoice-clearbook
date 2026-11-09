"""Calls to the Zoho Books API for one organisation, and what a failure means.

Every call names the organisation (`organization_id`) and goes to the connection's data centre.
Money is sent as exact JSON numbers from Decimal (rule 1: never through float). Errors never
carry the access token or document contents; Zoho's own message is kept, because it tells the
accountant what to fix.
"""

import json
import re
from decimal import Decimal
from typing import Any

import httpx

from app.adapters.accounting.zoho import regions

TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 200  # Zoho's largest page
MAX_PAGES = 50  # 10,000 rows: far above any client's chart or suppliers
WAIT_SECONDS = 60  # Zoho's limits are per minute and say nothing about when to try again
_DECIMAL = re.compile(r'"__decimal__(-?\d+(?:\.\d+)?)"')


class ZohoApiError(Exception):
    """Zoho refused a request or could not be reached.

    temporary: worth trying again later (rate limit, timeout, server error, access to renew);
    otherwise Zoho refused the content and `code` and the message say why. `access`: Zoho
    refused the access token, 401 (renew it) or 403 (a permission is missing)."""

    def __init__(
        self,
        message: str,
        *,
        temporary: bool,
        code: int | None = None,
        retry_after: int | None = None,
        access: int | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status  # the HTTP status Zoho answered, when it answered
        self.temporary = temporary
        self.code = code
        self.retry_after = retry_after
        self.access = access


class ZohoClient:
    def __init__(
        self,
        access_token: str,
        organisation_id: str,
        region: str | None,
        http: httpx.Client | None = None,
    ) -> None:
        self._url = regions.api_url(region)
        self._organisation_id = organisation_id
        self._headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def get(self, resource: str, **params: Any) -> dict[str, Any]:
        return self._send("GET", resource, params=params)

    def post(self, resource: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        content = to_json(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        return self._send("POST", resource, params={}, content=content, headers=headers)

    def post_file(
        self, resource: str, name: str, content: bytes, mime_type: str, field: str = "attachment"
    ) -> dict[str, Any]:
        """Upload one file as the form field `field` (an expense's receipt goes as "receipt")."""
        return self._send("POST", resource, params={},
                          files={field: (name, content, mime_type)})  # fmt: skip

    def pages(self, resource: str, key: str, **params: Any) -> list[dict[str, Any]]:
        """Every row of a list, page by page."""
        found: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            body = self.get(resource, page=page, per_page=PAGE_SIZE, **params)
            found += body.get(key) or []
            if not (body.get("page_context") or {}).get("has_more_page"):
                break
        return found

    def _send(
        self,
        method: str,
        resource: str,
        *,
        params: dict[str, Any],
        content: str | None = None,
        headers: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._http.request(
                method,
                f"{self._url}/{resource}",
                params={**params, "organization_id": self._organisation_id},
                content=content,
                files=files,
                headers={**self._headers, **(headers or {})},
            )
        except httpx.HTTPError as exc:
            raise ZohoApiError(f"cannot reach Zoho ({type(exc).__name__})", temporary=True) from exc
        body = _json(response)
        if not response.is_error and body.get("code", 0) == 0:
            return body
        raise _error(resource.split("/")[0], response, body)


def _error(resource: str, response: httpx.Response, body: dict[str, Any]) -> ZohoApiError:
    status = response.status_code
    code = body.get("code") if isinstance(body.get("code"), int) else None
    message = str(body.get("message") or "no reason given")[:300]
    if status == httpx.codes.TOO_MANY_REQUESTS:
        return ZohoApiError(
            f"Zoho's request limit was reached ({message}): try again later",
            temporary=True,
            code=code,
            retry_after=WAIT_SECONDS,
        )
    if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        return ZohoApiError(
            f"Zoho refused access to {resource} (HTTP {status}): {message}",
            temporary=True,  # fixed by renewing or reconnecting, not by changing the document
            code=code,
            access=status,
        )
    if status >= httpx.codes.INTERNAL_SERVER_ERROR:
        return ZohoApiError(f"Zoho answered HTTP {status} for {resource}", temporary=True)
    return ZohoApiError(
        f"Zoho refused {resource} (HTTP {status}): {message}",
        temporary=False,
        code=code,
        status=status,
    )


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def to_json(body: dict[str, Any]) -> str:
    """JSON with Decimals as exact numbers (json.dumps would need float)."""
    return _DECIMAL.sub(r"\1", json.dumps(body, default=_decimal_marker))


def _decimal_marker(value: object) -> str:
    if isinstance(value, Decimal):
        return f"__decimal__{value:f}"
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")
