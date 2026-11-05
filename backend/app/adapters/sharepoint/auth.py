"""Signing in to Microsoft for SharePoint (decision 83): OAuth 2.0 authorisation code flow against
Missan's own Entra ID tenant (single tenant), delegated permissions.

The scopes are fixed here, not in settings: they only read (sites, files, who signed in), so
even a mistake elsewhere cannot make the app change anything in SharePoint. A refresh token is
replaced at each renewal (Microsoft rotates them); it lapses after 90 days unused. Tokens are
never logged; they are returned to the caller, which stores them encrypted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings

LOGIN_URL = "https://login.microsoftonline.com"
# Read only, and nothing else: see the module docstring.
SCOPES = "offline_access User.Read Sites.Read.All Files.Read.All"
TIMEOUT_SECONDS = 20.0
# Microsoft's answer when a refresh token is revoked, expired or no longer allowed.
REVOKED = "invalid_grant"


class SharePointAuthError(Exception):
    """Microsoft refused the sign-in or could not be reached. Never carries a token."""


class SharePointAccessRevoked(SharePointAuthError):  # noqa: N818 - a state, read as a sentence
    """The stored sign-in no longer works (revoked, password changed, 90 days unused)."""


@dataclass(frozen=True, slots=True)
class MicrosoftTokens:
    access_token: str
    refresh_token: str
    expires_at: datetime


class MicrosoftAuth:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def configured(self) -> bool:
        s = self._settings
        return bool(
            s.sharepoint_tenant_id
            and s.sharepoint_client_id
            and s.sharepoint_client_secret.get_secret_value()
        )

    def authorize_url(self, state: str) -> str:
        query = {
            "client_id": self._settings.sharepoint_client_id,
            "response_type": "code",
            "redirect_uri": self._settings.sharepoint_redirect_uri,
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
            "prompt": "select_account",  # never sign in silently as whoever the browser knows
        }
        return f"{self._tenant_url()}/authorize?{urlencode(query)}"

    def exchange(self, code: str) -> MicrosoftTokens:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._settings.sharepoint_redirect_uri,
        }
        return self._token(form, refreshing=False)

    def refresh(self, refresh_token: str) -> MicrosoftTokens:
        return self._token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}, refreshing=True
        )

    def _token(self, form: dict[str, str], *, refreshing: bool) -> MicrosoftTokens:
        form |= {
            "client_id": self._settings.sharepoint_client_id,
            "client_secret": self._settings.sharepoint_client_secret.get_secret_value(),
            "scope": SCOPES,
        }
        try:
            response = self._http.post(f"{self._tenant_url()}/token", data=form)
        except httpx.HTTPError as exc:
            raise SharePointAuthError(f"cannot reach Microsoft ({type(exc).__name__})") from exc
        body = _json(response)
        if response.is_error or "access_token" not in body:
            reason = str(body.get("error") or "no details")[:80]
            error = (
                SharePointAccessRevoked if refreshing and reason == REVOKED else SharePointAuthError
            )
            raise error(f"Microsoft refused the sign-in (HTTP {response.status_code}): {reason}")
        return MicrosoftTokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or "",
            expires_at=datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 3600))),
        )

    def _tenant_url(self) -> str:
        return f"{LOGIN_URL}/{self._settings.sharepoint_tenant_id}/oauth2/v2.0"


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
