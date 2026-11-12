"""Login (decision 71): log in, log out, who is logged in; the dependencies every router uses.

The session is a signed token in an httpOnly, SameSite=Strict cookie: page scripts cannot read
it, and the browser sends it with page images too. Every request that changes something must
also carry the X-Clearbook header, which another site cannot add: a logged-in browser cannot be
made to approve or delete from elsewhere.

Decision 93: someone whose password an admin set can only see who they are, log out and
change it (`signed_in_user`); every other route answers 403 until they have (`current_user`).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from redis import Redis, RedisError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.redis import get_redis
from app.core.security import SecretNotSetError, create_token, read_token
from app.domain.enums import Role
from app.models import User
from app.services import users

COOKIE = "clearbook_session"
CHANGE_HEADER = "X-Clearbook"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

router = APIRouter(prefix="/auth", tags=["login"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_login_redis() -> Redis:
    """Where wrong attempts are counted (replaced in tests)."""
    return get_redis()


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


class MeOut(BaseModel):
    email: str
    name: str
    role: Role
    must_change_password: bool  # decision 93: nothing else opens until it is changed


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


def changes_allowed(request: Request) -> None:
    """A change must name this app in a header another site cannot set."""
    if request.method not in SAFE_METHODS and request.headers.get(CHANGE_HEADER) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "request refused: missing X-Clearbook")


def signed_in_user(request: Request, session: SessionDep, settings: SettingsDep) -> User:
    """The logged-in, active user, even one who must still change their password; 401
    otherwise."""
    changes_allowed(request)
    token = request.cookies.get(COOKIE)
    try:
        signed = read_token(settings, token) if token else None
    except SecretNotSetError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    user = users.active_user(session, signed.user_id, signed.token_version) if signed else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "log in first")
    return user


def current_user(user: Annotated[User, Depends(signed_in_user)]) -> User:
    """The logged-in user, password changed if it had to be. Every protected router depends
    on it."""
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "change your password first")
    return user


def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role is not Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an admin can do this")
    return user


SignedInDep = Annotated[User, Depends(signed_in_user)]
UserDep = Annotated[User, Depends(current_user)]
AdminDep = Annotated[User, Depends(require_admin)]


@router.post("/login", dependencies=[Depends(changes_allowed)])
def login(
    body: LoginBody,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    redis: Annotated[Redis, Depends(get_login_redis)],
) -> MeOut:
    """401 for a wrong email or password (never which), 429 while locked."""
    try:
        user = users.authenticate(
            session, settings, redis, email=body.email, password=body.password
        )
        token = create_token(settings, user.id, user.token_version)
    except users.LoginLocked as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except users.LoginFailed as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except (SecretNotSetError, RedisError) as exc:
        detail = str(exc) if isinstance(exc, SecretNotSetError) else "login is unavailable: Redis"
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail) from exc
    session.commit()
    _set_session(response, settings, token)
    return _me(user)


@router.post("/password")
def change_password(
    body: PasswordChange,
    user: SignedInDep,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    redis: Annotated[Redis, Depends(get_login_redis)],
) -> MeOut:
    """Decision 93: the logged-in person replaces their password. Other sessions end; this one
    stays logged in. 400 when refused (never 401: that would log this session out), 429 while
    locked."""
    try:
        users.change_own_password(
            session, settings, redis, user, current=body.current_password, new=body.new_password
        )
        token = create_token(settings, user.id, user.token_version)
    except users.LoginLocked as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except users.UserError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except RedisError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable: Redis") from exc
    session.commit()
    _set_session(response, settings, token)
    return _me(user)


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(changes_allowed)]
)
def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE, path="/", samesite="strict", httponly=True)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
def me(user: SignedInDep) -> MeOut:
    return _me(user)


def _set_session(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _me(user: User) -> MeOut:
    return MeOut(
        email=user.email,
        name=user.name,
        role=user.role,
        must_change_password=user.must_change_password,
    )
