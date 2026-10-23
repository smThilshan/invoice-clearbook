"""Connecting a client's Xero organisation (task 5.3): OAuth 2.0 authorisation code flow.

The user approves the Workbench in Xero's login page; Xero sends them back with a code, which
is exchanged for an access token (30 minutes) and a refresh token (renewed on use). The
organisations ("tenants") the user approved are listed by /connections. Tokens are never
logged; they are returned to the caller, which stores them encrypted.
"""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"  # noqa: S105 - an address, not a password
CONNECTIONS_URL = "https://api.xero.com/connections"
TIMEOUT_SECONDS = 20.0


class XeroAuthError(Exception):
    """Xero refused or could not be reached. Never carries a token."""


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: str
    auth_event_id: str | None  # which approval these tokens came from


@dataclass(frozen=True, slots=True)
class Tenant:
    connection_id: str  # needed to disconnect
    tenant_id: str
    name: str
    auth_event_id: str | None


class XeroAuth:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def configured(self) -> bool:
        return bool(
            self._settings.xero_client_id and self._settings.xero_client_secret.get_secret_value()
        )

    def authorize_url(self, state: str) -> str:
        query = {
            "response_type": "code",
            "client_id": self._settings.xero_client_id,
            "redirect_uri": self._settings.xero_redirect_uri,
            "scope": self._settings.xero_scopes,
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(query)}"

    def exchange(self, code: str) -> Tokens:
        return self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.xero_redirect_uri,
            }
        )

    def refresh(self, refresh_token: str) -> Tokens:
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def tenants(self, access_token: str) -> list[Tenant]:
        body = self._call("GET", CONNECTIONS_URL, access_token)
        return [
            Tenant(t["id"], t["tenantId"], t.get("tenantName") or "", t.get("authEventId"))
            for t in body
            if t.get("tenantType", "ORGANISATION") == "ORGANISATION"
        ]

    def disconnect(self, access_token: str, connection_id: str) -> None:
        self._call("DELETE", f"{CONNECTIONS_URL}/{connection_id}", access_token)

    def _token(self, form: dict[str, str]) -> Tokens:
        secret = self._settings.xero_client_secret.get_secret_value()
        try:
            response = self._http.post(
                TOKEN_URL, data=form, auth=(self._settings.xero_client_id, secret)
            )
        except httpx.HTTPError as exc:
            raise XeroAuthError(f"cannot reach Xero ({type(exc).__name__})") from exc
        if response.is_error:
            raise XeroAuthError(
                f"Xero refused the sign-in (HTTP {response.status_code}): {_reason(response)}"
            )
        body = response.json()
        access = body["access_token"]
        return Tokens(
            access_token=access,
            refresh_token=body["refresh_token"],
            expires_at=datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 1800))),
            scopes=body.get("scope", ""),
            auth_event_id=_claim(access, "authentication_event_id"),
        )

    def _call(self, method: str, url: str, access_token: str) -> Any:
        try:
            response = self._http.request(
                method, url, headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx.HTTPError as exc:
            raise XeroAuthError(f"cannot reach Xero ({type(exc).__name__})") from exc
        if response.is_error:
            raise XeroAuthError(f"Xero answered HTTP {response.status_code}: {_reason(response)}")
        return response.json() if response.content else None


def _reason(response: httpx.Response) -> str:
    """Xero's short error code ("invalid_grant"), never a token or a body echo."""
    try:
        body = response.json()
    except ValueError:
        return "no details"
    reason = body.get("error") or body.get("Title") or body.get("Detail") or "no details"
    return str(reason)[:120]


def _claim(jwt: str, name: str) -> str | None:
    """One claim from a JWT's payload (not verified: only used to pick the tenant)."""
    try:
        payload = jwt.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return None
    value = data.get(name)
    return value if isinstance(value, str) else None
