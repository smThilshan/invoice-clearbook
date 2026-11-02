"""Passwords and session tokens (decision 71). Pure functions: no database, no web framework.

Passwords: Argon2id (argon2-cffi's defaults follow the RFC 9106 recommendation); a hash made
with older settings is reported so the caller can store a fresh one. Sessions: a JWT (HS256)
naming the user and their token_version, valid for settings.session_hours. Neither a password
nor a token is ever logged.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
PLACEHOLDERS = frozenset({"change-me", "changeme", "secret"})

_hasher = PasswordHasher()
# Checked against when the email is unknown, so a wrong email and a wrong password take as long.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


class SecretNotSetError(Exception):
    """JWT_SECRET is missing, the placeholder or too short: nobody can log in until it is set."""


@dataclass(frozen=True, slots=True)
class Session:
    user_id: uuid.UUID
    token_version: int


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """None stands for an unknown user: the same work is done, and the answer is False."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_token(settings: Settings, user_id: uuid.UUID, token_version: int) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(hours=settings.session_hours),
    }
    return jwt.encode(claims, _secret(settings), algorithm=ALGORITHM)


def read_token(settings: Settings, token: str) -> Session | None:
    """None: missing, tampered, signed with another secret, or expired."""
    try:
        claims = jwt.decode(
            token, _secret(settings), algorithms=[ALGORITHM], options={"require": ["sub", "exp"]}
        )
        return Session(uuid.UUID(claims["sub"]), int(claims.get("ver", 0)))
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None


def _secret(settings: Settings) -> str:
    secret = settings.jwt_secret.get_secret_value()
    if len(secret) < MIN_SECRET_LENGTH or secret.lower() in PLACEHOLDERS:
        raise SecretNotSetError("JWT_SECRET is not set in .env: create one with `make jwt-secret`")
    return secret
