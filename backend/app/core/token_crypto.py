"""Stored sign-ins (accounting systems, SharePoint): tokens encrypted with settings.token_key
before they reach the database, and sign-in `state` values signed with it, so a sign-in that was
not started here is refused. Pure functions; nothing here is ever logged."""

import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


class TokenKeyError(Exception):
    """TOKEN_KEY is missing or wrong, or a stored token cannot be read with it."""


def cipher(settings: Settings) -> Fernet:
    key = settings.token_key.get_secret_value()
    if not key:
        raise TokenKeyError("TOKEN_KEY is not set in .env: create one with `make secret-key`")
    try:
        return Fernet(key)
    except ValueError as exc:
        raise TokenKeyError("TOKEN_KEY in .env is not a valid key: `make secret-key`") from exc


def encrypt(settings: Settings, value: str) -> str:
    return cipher(settings).encrypt(value.encode()).decode()


def decrypt(settings: Settings, value: str) -> str:
    try:
        return cipher(settings).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenKeyError(
            "stored access cannot be read (TOKEN_KEY changed?): connect again"
        ) from exc


def sign(settings: Settings, body: str) -> str:
    cipher(settings)  # no TOKEN_KEY: refuse, never sign with an empty or borrowed key
    key = settings.token_key.get_secret_value()
    return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
