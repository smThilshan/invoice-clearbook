"""Missan users (decision 71): create, log in, deactivate, change a password.

Logging in never says whether the email exists or the account is deactivated: one message,
the same work. After settings.login_max_failures wrong attempts for an email it is locked for
settings.login_lock_minutes; the count lives in Redis, shared by every API process. Changing a
password or deactivating raises token_version: every session signed before stops working.
"""

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import cast

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password, needs_rehash, verify_password
from app.domain.enums import Role
from app.models import User

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
WRONG = "Wrong email or password."


class UserError(Exception):
    """The request cannot be done; the message is safe to show."""


class LoginFailed(UserError):  # noqa: N818 - an outcome, read as a sentence
    pass


class LoginLocked(LoginFailed):
    pass


def create_user(session: Session, *, email: str, name: str, role: Role, password: str) -> User:
    """The caller commits."""
    email = normalized(email)
    if not EMAIL.match(email):
        raise UserError(f"{email!r} is not an email address")
    if not name.strip():
        raise UserError("a name is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"the password must have at least {MIN_PASSWORD_LENGTH} characters")
    if find(session, email) is not None:
        raise UserError(f"a user with {email} already exists")
    user = User(email=email, name=name.strip(), role=role, password_hash=hash_password(password))
    session.add(user)
    session.flush()
    return user


def authenticate(
    session: Session, settings: Settings, redis: Redis, *, email: str, password: str
) -> User:
    """The active user with this email and password, or LoginFailed. The caller commits."""
    email = normalized(email)
    key = f"login-failures:{email}"
    failures = cast(bytes | None, redis.get(key))  # sync client: never an awaitable here
    if int(failures or 0) >= settings.login_max_failures:
        raise LoginLocked(
            f"Too many wrong attempts: try again in {settings.login_lock_minutes} minutes."
        )
    user = find(session, email)
    usable = user if user is not None and user.is_active else None
    # Always one Argon2 check (against a dummy hash for no usable user): the same time either way.
    password_ok = verify_password(usable.password_hash if usable else None, password)
    if usable is None or not password_ok:
        if redis.incr(key) == 1:
            redis.expire(key, settings.login_lock_minutes * 60)
        logger.info("login refused")  # never the email or password
        raise LoginFailed(WRONG)
    redis.delete(key)
    if needs_rehash(usable.password_hash):
        usable.password_hash = hash_password(password)
    usable.last_login_at = datetime.now(UTC)
    session.flush()
    return usable


def active_user(session: Session, user_id: uuid.UUID, token_version: int) -> User | None:
    """The user a session names, if still active and the session not withdrawn since."""
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.token_version != token_version:
        return None
    return user


def set_password(session: Session, email: str, password: str) -> User:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"the password must have at least {MIN_PASSWORD_LENGTH} characters")
    user = _existing(session, email)
    user.password_hash = hash_password(password)
    user.token_version += 1
    session.flush()
    return user


def deactivate(session: Session, email: str) -> User:
    user = _existing(session, email)
    user.is_active = False
    user.token_version += 1
    session.flush()
    return user


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.email)))


def find(session: Session, email: str) -> User | None:
    return session.scalars(select(User).where(func.lower(User.email) == normalized(email))).first()


def normalized(email: str) -> str:
    return email.strip().lower()


def _existing(session: Session, email: str) -> User:
    user = find(session, email)
    if user is None:
        raise UserError(f"no user with {normalized(email)}")
    return user
