"""Passwords and session tokens (decision 71): no database, made-up values only."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    SecretNotSetError,
    create_token,
    hash_password,
    needs_rehash,
    read_token,
    verify_password,
)

SETTINGS = Settings(_env_file=None, jwt_secret="made-up-secret-" + "x" * 40)
USER = uuid.uuid4()


def test_a_password_is_stored_only_as_an_argon2id_hash_and_checked_against_it() -> None:
    stored = hash_password("correct horse battery")

    assert stored.startswith("$argon2id$") and "correct horse" not in stored
    assert verify_password(stored, "correct horse battery")
    assert not verify_password(stored, "wrong horse battery")
    assert not needs_rehash(stored)


def test_an_unknown_user_never_matches_whatever_is_typed() -> None:
    assert not verify_password(None, "not-a-real-password")  # even the dummy hash's own password
    assert not verify_password("not-a-hash", "anything")


def test_a_session_token_names_the_user_and_their_token_version() -> None:
    session = read_token(SETTINGS, create_token(SETTINGS, USER, 3))

    assert session is not None and (session.user_id, session.token_version) == (USER, 3)


def test_an_expired_tampered_or_foreign_token_is_no_session() -> None:
    secret = SETTINGS.jwt_secret.get_secret_value()
    past = datetime.now(UTC) - timedelta(minutes=1)
    expired = jwt.encode({"sub": str(USER), "ver": 0, "exp": past}, secret, "HS256")
    token = create_token(SETTINGS, USER, 0)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    other = Settings(_env_file=None, jwt_secret="another-made-up-secret-" + "y" * 40)

    assert read_token(SETTINGS, expired) is None
    assert read_token(SETTINGS, tampered) is None
    assert read_token(other, token) is None
    assert read_token(SETTINGS, "not a token") is None


@pytest.mark.parametrize("secret", ["", "change-me", "too-short-secret"])
def test_no_session_is_signed_without_a_real_secret(secret: str) -> None:
    with pytest.raises(SecretNotSetError, match="make jwt-secret"):
        create_token(Settings(_env_file=None, jwt_secret=secret), USER, 0)
