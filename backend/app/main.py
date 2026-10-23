"""FastAPI application factory. Run with `uvicorn app.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import clients, documents, folders, health, runs, xero
from app.core.config import Settings, get_settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env == "prod")

    is_prod = settings.app_env == "prod"
    app = FastAPI(
        title="invoice-clearbook",
        version=__version__,
        lifespan=_lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(clients.router)
    app.include_router(folders.router)
    app.include_router(xero.router)
    app.include_router(documents.router)
    return app
