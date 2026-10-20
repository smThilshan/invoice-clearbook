"""Liveness and readiness probes."""

from contextlib import closing
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.llm import ModelStatus, get_extraction_client
from app.core.config import Settings, get_settings
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
    # Informational: the API and review work without the model; extraction waits for it.
    model: ModelStatus


def get_model_status(settings: Annotated[Settings, Depends(get_settings)]) -> ModelStatus:
    with closing(get_extraction_client(settings)) as client:
        return client.check()


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
    model: Annotated[ModelStatus, Depends(get_model_status)],
    response: Response,
) -> Readiness:
    """Postgres and Redis decide readiness (503 if either is down). The model is reported
    too (installed, Instruct, with vision), but does not make the API unready."""
    database: CheckStatus = "ok" if ping(session) else "unavailable"
    redis: CheckStatus = "ok" if redis_ok else "unavailable"
    overall: CheckStatus = "ok" if database == redis == "ok" else "unavailable"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status=overall, database=database, redis=redis, model=model)
