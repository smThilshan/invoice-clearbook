"""Calls to the Xero Accounting API for one organisation, and what a failure means.

Money is sent as exact JSON numbers from Decimal (rule 1: never through float). Errors never
carry the access token or document contents; Xero's own validation messages are kept, because
they tell the accountant what to fix.
"""

import json
import re
from decimal import Decimal
from typing import Any

import httpx

API_URL = "https://api.xero.com/api.xro/2.0"
TIMEOUT_SECONDS = 30.0
_DECIMAL = re.compile(r'"__decimal__(-?\d+(?:\.\d+)?)"')


class XeroApiError(Exception):
    """Xero refused a request or could not be reached.

    temporary: worth trying again later (rate limit, timeout, server error, access to renew);
    otherwise Xero refused the content and `messages` says why."""

    def __init__(
        self,
        message: str,
        *,
        temporary: bool,
        messages: list[str] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.temporary = temporary
        self.messages = messages or []
        self.retry_after = retry_after


class XeroClient:
    def __init__(self, access_token: str, tenant_id: str, http: httpx.Client | None = None) -> None:
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "xero-tenant-id": tenant_id,
            "Accept": "application/json",
        }
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def get(self, resource: str, **params: Any) -> dict[str, Any]:
        return self._send("GET", resource, params=params)

    def put(
        self, resource: str, body: dict[str, Any], *, key: str | None = None, **params: Any
    ) -> dict[str, Any]:
        """Create. `key` is Xero's Idempotency-Key: the same key again returns the first result."""
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        return self._send("PUT", resource, params=params, content=to_json(body), headers=headers)

    def put_file(self, resource: str, content: bytes, mime_type: str) -> dict[str, Any]:
        """Upload a file as the raw request body (Xero attachments)."""
        return self._send("PUT", resource, params={}, content=content,
                          headers={"Content-Type": mime_type})  # fmt: skip

    def _send(
        self,
        method: str,
        resource: str,
        *,
        params: dict[str, Any],
        content: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._http.request(
                method,
                f"{API_URL}/{resource}",
                params=params,
                content=content,
                headers={**self._headers, **(headers or {})},
            )
        except httpx.HTTPError as exc:
            raise XeroApiError(f"cannot reach Xero ({type(exc).__name__})", temporary=True) from exc
        if not response.is_error:
            body: dict[str, Any] = response.json()
            return body
        raise _error(resource.split("/")[0], response)


def _error(resource: str, response: httpx.Response) -> XeroApiError:
    code = response.status_code
    if code == httpx.codes.TOO_MANY_REQUESTS:
        wait = response.headers.get("Retry-After", "")
        return XeroApiError(
            "Xero's request limit was reached: try again in a minute",
            temporary=True,
            retry_after=int(wait) if wait.isdigit() else None,
        )
    if code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        return XeroApiError(
            f"Xero refused access to {resource} (HTTP {code}): reconnect Xero and allow every "
            "permission asked",
            temporary=True,  # fixed by reconnecting, not by changing the document
        )
    if code >= httpx.codes.INTERNAL_SERVER_ERROR:
        return XeroApiError(f"Xero answered HTTP {code} for {resource}", temporary=True)
    messages = validation_messages(response)
    return XeroApiError(
        f"Xero refused {resource} (HTTP {code}): {'; '.join(messages) or 'no reason given'}",
        temporary=False,
        messages=messages,
    )


def validation_messages(response: httpx.Response) -> list[str]:
    """Xero's reasons, from `Elements[].ValidationErrors[].Message` or the top `Message`."""
    try:
        body = response.json()
    except ValueError:
        return []
    found = [
        error["Message"]
        for element in body.get("Elements") or []
        for error in element.get("ValidationErrors") or []
        if error.get("Message")
    ]
    if not found and body.get("Message"):
        found = [body["Message"]]
    return list(dict.fromkeys(found))  # each reason once, in order


def to_json(body: dict[str, Any]) -> str:
    """JSON with Decimals as exact numbers (json.dumps would need float)."""
    text = json.dumps(body, default=_decimal_marker)
    return _DECIMAL.sub(r"\1", text)


def _decimal_marker(value: object) -> str:
    if isinstance(value, Decimal):
        return f"__decimal__{value:f}"
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")
