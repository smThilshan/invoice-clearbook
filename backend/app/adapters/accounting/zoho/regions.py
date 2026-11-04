"""Zoho's data centres: an organisation's data lives in one, named by its domain ("com", "ae").

The sign-in tells us which one (the `accounts-server` it sends back). Only the data centres
listed here are accepted, so a forged address can never receive our client secret or a token.
"""

from urllib.parse import urlparse

# Zoho Books API data centres (Zoho Books API v3, introduction).
REGIONS = frozenset({"com", "eu", "in", "com.au", "jp", "ca", "com.cn", "sa", "ae"})
DEFAULT = "com"


def from_accounts_server(url: str | None) -> str | None:
    """The region of a Zoho accounts address (https://accounts.zoho.ae: "ae"); None for anything
    that is not a Zoho data centre."""
    if not url:
        return DEFAULT  # Zoho omits it for its main data centre
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host.startswith("accounts.zoho."):
        return None
    region = host.removeprefix("accounts.zoho.")
    return region if region in REGIONS else None


def accounts_url(region: str | None) -> str:
    return f"https://accounts.zoho.{_known(region)}"


def api_url(region: str | None) -> str:
    return f"https://www.zohoapis.{_known(region)}/books/v3"


def _known(region: str | None) -> str:
    region = region or DEFAULT
    if region not in REGIONS:
        raise ValueError(f"not a Zoho data centre: {region!r}")
    return region
