"""Connecting a client's Zoho Books organisation (decision 74): OAuth 2.0 authorisation code flow.

The user approves the Workbench on Zoho's sign-in page, which sends them back with a code and the
data centre their account lives in (`accounts-server`). The code is exchanged there for an access
token (1 hour) and a refresh token (valid until revoked). One sign-in reaches every organisation
the user can open; /organizations lists them. Tokens are never logged; they are returned to the
caller, which stores them encrypted.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.adapters.accounting.base import AccessRevoked, ConnectorError, Organisation, Tokens
from app.adapters.accounting.zoho import regions
from app.core.config import Settings

TIMEOUT_SECONDS = 20.0
# Zoho answers HTTP 200 with {"error": "invalid_code"} for a code or refresh token it no longer
# accepts (used, expired, revoked).
REVOKED = "invalid_code"


class ZohoAuthError(ConnectorError):
    """Zoho refused or could not be reached. Never carries a token."""


class ZohoAccessRevoked(ZohoAuthError, AccessRevoked):  # noqa: N818 - see AccessRevoked
    """The refresh token was revoked (by the user in Zoho, or replaced by too many sign-ins)."""


class ZohoAuth:
    """The Connector port for Zoho Books."""

    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def configured(self) -> bool:
        return bool(
            self._settings.zoho_client_id and self._settings.zoho_client_secret.get_secret_value()
        )

    def authorize_url(self, state: str) -> str:
        query = {
            "response_type": "code",
            "client_id": self._settings.zoho_client_id,
            "redirect_uri": self._settings.zoho_redirect_uri,
            "scope": self._settings.zoho_scopes,
            "access_type": "offline",  # a refresh token
            "prompt": "consent",
            "state": state,
        }
        # Zoho's main data centre sends users of the others on to theirs (Multi-DC).
        return f"{regions.accounts_url(regions.DEFAULT)}/oauth/v2/auth?{urlencode(query)}"

    def exchange(self, code: str, returned: Mapping[str, str] | None = None) -> Tokens:
        region = regions.from_accounts_server((returned or {}).get("accounts-server"))
        if region is None:
            raise ZohoAuthError("the sign-in named a Zoho data centre we do not know")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._settings.zoho_redirect_uri,
        }
        return self._token(form, region, refresh_token=None)

    def refresh(self, refresh_token: str, region: str | None = None) -> Tokens:
        form = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        return self._token(form, region, refresh_token=refresh_token)

    def organisations(self, access_token: str, region: str | None = None) -> list[Organisation]:
        body = self._call("GET", f"{regions.api_url(region)}/organizations", access_token)
        return [
            Organisation(None, str(o["organization_id"]), o.get("name") or "", None)
            for o in body.get("organizations", [])
        ]

    def disconnect(
        self,
        access_token: str,
        connection_id: str | None,
        *,
        refresh_token: str = "",
        region: str | None = None,
    ) -> None:
        """Revoking the refresh token removes the app's access to the user's Zoho."""
        if not refresh_token:
            return
        url = f"{regions.accounts_url(region)}/oauth/v2/token/revoke"
        try:
            response = self._http.post(url, params={"token": refresh_token})
        except httpx.HTTPError as exc:
            raise ZohoAuthError(f"cannot reach Zoho ({type(exc).__name__})") from exc
        if response.is_error:
            raise ZohoAuthError(f"Zoho answered HTTP {response.status_code}: {_reason(response)}")

    def _token(
        self, form: dict[str, str], region: str | None, *, refresh_token: str | None
    ) -> Tokens:
        form |= {
            "client_id": self._settings.zoho_client_id,
            "client_secret": self._settings.zoho_client_secret.get_secret_value(),
        }
        try:
            response = self._http.post(f"{regions.accounts_url(region)}/oauth/v2/token", data=form)
        except httpx.HTTPError as exc:
            raise ZohoAuthError(f"cannot reach Zoho ({type(exc).__name__})") from exc
        body = _json(response)
        if response.is_error or "access_token" not in body:
            reason = _reason(response)
            error = ZohoAccessRevoked if reason == REVOKED and refresh_token else ZohoAuthError
            raise error(f"Zoho refused the sign-in (HTTP {response.status_code}): {reason}")
        return Tokens(
            access_token=body["access_token"],
            # Zoho sends a refresh token only with the first exchange; renewing keeps the old one.
            refresh_token=body.get("refresh_token") or refresh_token or "",
            expires_at=datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 3600))),
            scopes=body.get("scope") or self._settings.zoho_scopes,
            approval_id=None,  # Zoho does not say which organisation was meant
            region=region or regions.DEFAULT,
        )

    def _call(self, method: str, url: str, access_token: str) -> Any:
        try:
            response = self._http.request(
                method, url, headers={"Authorization": f"Zoho-oauthtoken {access_token}"}
            )
        except httpx.HTTPError as exc:
            raise ZohoAuthError(f"cannot reach Zoho ({type(exc).__name__})") from exc
        if response.is_error:
            raise ZohoAuthError(f"Zoho answered HTTP {response.status_code}: {_reason(response)}")
        return _json(response)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _reason(response: httpx.Response) -> str:
    """Zoho's short error ("invalid_code") or message, never a token or a body echo."""
    body = _json(response)
    reason = body.get("error") or body.get("message") or "no details"
    return str(reason)[:120]
