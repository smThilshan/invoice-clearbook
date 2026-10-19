"""Liveness and readiness probes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_session, ping

router = APIRouter(prefix="/health", tags=["health"])

CheckStatus = Literal["ok", "unavailable"]


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: CheckStatus
    database: CheckStatus


@router.get("")
def liveness() -> Liveness:
    """The process is up. Checks no dependencies."""
    return Liveness()


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
def readiness(session: Annotated[Session, Depends(get_session)], response: Response) -> Readiness:
    """Dependencies are reachable. Redis is added with Celery (task 2.2)."""
    database: CheckStatus = "ok" if ping(session) else "unavailable"
    if database != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status=database, database=database)
