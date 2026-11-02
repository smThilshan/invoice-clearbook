"""FastAPI application factory. Run with `uvicorn app.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app import __version__
from app.api import accounting, auth, clients, documents, folders, health, runs
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
    # Open: probes, logging in, and the accounting system's return (signed state). Everything
    # else needs a logged-in user (decision 71); tests/test_routes_need_login.py keeps it so.
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(accounting.callback_router)
    logged_in = [Depends(auth.current_user)]
    for module in (runs, clients, folders, accounting, documents):
        app.include_router(module.router, dependencies=logged_in)
    return app
