"""Liveness and readiness probes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_session, ping
from app.core.redis import ping_redis

router = APIRouter(prefix="/health", tags=["health"])

CheckStatus = Literal["ok", "unavailable"]


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: CheckStatus
    database: CheckStatus
    redis: CheckStatus


@router.get("")
def liveness() -> Liveness:
    """The process is up. Checks no dependencies."""
    return Liveness()


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
def readiness(
    session: Annotated[Session, Depends(get_session)],
    redis_ok: Annotated[bool, Depends(ping_redis)],
    response: Response,
) -> Readiness:
    """Dependencies are reachable: Postgres and Redis (job queue)."""
    database: CheckStatus = "ok" if ping(session) else "unavailable"
    redis: CheckStatus = "ok" if redis_ok else "unavailable"
    overall: CheckStatus = "ok" if database == redis == "ok" else "unavailable"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status=overall, database=database, redis=redis)
