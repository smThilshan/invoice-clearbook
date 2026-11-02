"""API tests act as a logged-in user (decision 71), except the ones about logging in."""

from fastapi import FastAPI

from app.api.auth import current_user
from app.domain.enums import Role
from app.models import User


def log_in(app: FastAPI, role: Role = Role.ADMIN, email: str = "tester@missan.test") -> User:
    """Every protected route sees this user, as if their session cookie had been sent."""
    user = User(email=email, name="Made-up Tester", role=role, password_hash="-",
                is_active=True, token_version=0)  # fmt: skip
    app.dependency_overrides[current_user] = lambda: user
    return user
