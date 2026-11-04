"""Zoho Books sign-in (decision 74) against a fake Zoho: no network, no real tokens."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters.accounting.base import AccessRevoked
from app.adapters.accounting.zoho import regions
from app.adapters.accounting.zoho.auth import ZohoAuth, ZohoAuthError
from app.core.config import Settings

SETTINGS = Settings(_env_file=None, zoho_client_id="app-id", zoho_client_secret="app-secret")


def fake_zoho(handler) -> ZohoAuth:  # type: ignore[no-untyped-def]
    return ZohoAuth(SETTINGS, httpx.Client(transport=httpx.MockTransport(handler)))


def test_the_sign_in_address_asks_for_offline_access_and_the_books_scopes() -> None:
    url = urlparse(fake_zoho(lambda r: httpx.Response(500)).authorize_url("s1"))
    query = parse_qs(url.query)

    assert url.netloc == "accounts.zoho.com"
    assert query["client_id"] == ["app-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/zoho/callback"]
    assert query["access_type"] == ["offline"] and query["state"] == ["s1"]
    assert "ZohoBooks.bills.ALL" in query["scope"][0]


def test_a_code_is_exchanged_at_the_data_centre_the_sign_in_named() -> None:
    seen: dict[str, object] = {}

    def token(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={
            "access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600,
            "api_domain": "https://www.zohoapis.ae",
        })  # fmt: skip

    tokens = fake_zoho(token).exchange(
        "code-1", {"accounts-server": "https://accounts.zoho.ae", "location": "ae"}
    )

    assert seen["host"] == "accounts.zoho.ae"
    assert seen["form"] == {
        "grant_type": ["authorization_code"], "code": ["code-1"],
        "redirect_uri": ["http://localhost:8000/zoho/callback"],
        "client_id": ["app-id"], "client_secret": ["app-secret"],
    }  # fmt: skip
    assert (tokens.refresh_token, tokens.region, tokens.approval_id) == ("refresh-1", "ae", None)


def test_a_data_centre_that_is_not_zohos_is_never_sent_the_secret() -> None:
    calls: list[httpx.Request] = []

    def anything(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "a"})

    for server in ("https://accounts.zoho.evil.com", "http://accounts.zoho.ae",
                   "https://accounts.zoho.xx", "https://evil.com"):  # fmt: skip
        with pytest.raises(ZohoAuthError, match="data centre we do not know"):
            fake_zoho(anything).exchange("code-1", {"accounts-server": server})

    assert calls == []


def test_renewing_keeps_the_refresh_token_and_the_data_centre() -> None:
    seen: dict[str, object] = {}

    def token(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        return httpx.Response(200, json={"access_token": "access-2", "expires_in": 3600})

    tokens = fake_zoho(token).refresh("refresh-1", "ae")

    assert seen["host"] == "accounts.zoho.ae"
    assert (tokens.access_token, tokens.refresh_token, tokens.region) == (
        "access-2",
        "refresh-1",
        "ae",
    )


def test_a_revoked_refresh_token_asks_for_a_new_sign_in() -> None:
    def revoked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "invalid_code"})  # Zoho: 200 with an error

    with pytest.raises(AccessRevoked):
        fake_zoho(revoked).refresh("refresh-1", "com")


def test_a_refused_code_says_why_without_tokens() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "invalid_client"})

    with pytest.raises(ZohoAuthError, match="invalid_client") as caught:
        fake_zoho(refused).exchange("code-1", {})

    assert not isinstance(caught.value, AccessRevoked)
    assert "app-secret" not in str(caught.value)


def test_organisations_are_listed_from_the_connections_data_centre() -> None:
    seen: dict[str, str] = {}

    def organizations(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"code": 0, "organizations": [
            {"organization_id": "905001", "name": "Missan Test"},
        ]})  # fmt: skip

    found = fake_zoho(organizations).organisations("access-1", "ae")

    assert seen == {
        "url": "https://www.zohoapis.ae/books/v3/organizations",
        "auth": "Zoho-oauthtoken access-1",
    }
    assert [(o.organisation_id, o.name, o.connection_id) for o in found] == [
        ("905001", "Missan Test", None)
    ]


def test_disconnecting_revokes_the_refresh_token() -> None:
    seen: dict[str, str] = {}

    def revoke(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"status": "success"})

    fake_zoho(revoke).disconnect("access-1", None, refresh_token="refresh-1", region="eu")

    assert seen["url"] == "https://accounts.zoho.eu/oauth/v2/token/revoke?token=refresh-1"


def test_unreachable_zoho_is_a_connector_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ZohoAuthError, match="cannot reach Zoho"):
        fake_zoho(down).refresh("refresh-1", "com")


def test_regions_name_only_zohos_data_centres() -> None:
    assert regions.from_accounts_server(None) == "com"
    assert regions.from_accounts_server("https://accounts.zoho.com.au") == "com.au"
    assert regions.api_url("sa") == "https://www.zohoapis.sa/books/v3"
    with pytest.raises(ValueError, match="not a Zoho data centre"):
        regions.api_url("evil.com")
