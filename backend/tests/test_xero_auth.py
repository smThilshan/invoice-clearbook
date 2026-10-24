"""Xero sign-in (task 5.3) against a fake Xero: no network, no real tokens."""

import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters.accounting.xero.auth import XeroAuth, XeroAuthError
from app.core.config import Settings

SETTINGS = Settings(_env_file=None, xero_client_id="app-id", xero_client_secret="app-secret")


def jwt(claims: dict[str, str]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def fake_xero(handler) -> XeroAuth:  # type: ignore[no-untyped-def]
    return XeroAuth(SETTINGS, httpx.Client(transport=httpx.MockTransport(handler)))


def test_the_sign_in_address_asks_for_the_configured_scopes() -> None:
    query = parse_qs(urlparse(fake_xero(lambda r: httpx.Response(500)).authorize_url("s1")).query)

    assert query["client_id"] == ["app-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/xero/callback"]
    assert "accounting.invoices" in query["scope"][0] and "offline_access" in query["scope"][0]
    assert query["state"] == ["s1"]


def test_a_code_becomes_tokens_with_the_approval_it_came_from() -> None:
    seen: dict[str, object] = {}

    def token(request: httpx.Request) -> httpx.Response:
        seen["form"] = parse_qs(request.content.decode())
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={
            "access_token": jwt({"authentication_event_id": "event-1"}),
            "refresh_token": "refresh-1", "expires_in": 1800, "scope": "offline_access",
        })  # fmt: skip

    tokens = fake_xero(token).exchange("code-1")

    assert seen["form"] == {"grant_type": ["authorization_code"], "code": ["code-1"],
                            "redirect_uri": ["http://localhost:8000/xero/callback"]}  # fmt: skip
    assert str(seen["auth"]).startswith("Basic ")
    assert (tokens.refresh_token, tokens.approval_id) == ("refresh-1", "event-1")


def test_a_refused_sign_in_says_why_without_tokens() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(XeroAuthError, match="invalid_grant"):
        fake_xero(refused).exchange("old-code")


def test_only_organisations_are_offered() -> None:
    def connections(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer access-1"
        return httpx.Response(200, json=[
            {"id": "c1", "tenantId": "t1", "tenantName": "Demo Company (Global)",
             "tenantType": "ORGANISATION", "authEventId": "event-1"},
            {"id": "c2", "tenantId": "t2", "tenantName": "Practice", "tenantType": "PRACTICE"},
        ])  # fmt: skip

    [tenant] = fake_xero(connections).organisations("access-1")

    assert (tenant.organisation_id, tenant.name, tenant.connection_id) == (
        "t1",
        "Demo Company (Global)",
        "c1",
    )
