"""Folders to run from (decision 35): the allowed places and what is inside them.

Read only. Paths are relative to a place and cannot leave it (see `services.places`).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.models import Client
from app.pipeline.folders import InboxError, folder_date_from_path
from app.services.clients import get_client
from app.services.places import browse, find_place, list_places

router = APIRouter(prefix="/folders", tags=["folders"])


class PlaceOut(BaseModel):
    key: str  # send it back as the run's `place`
    label: str  # e.g. "This Mac", "Shared drives", "Cafe Al Noor folder"
    path: str  # absolute, shown so the user knows where they are


class FolderOut(BaseModel):
    name: str
    path: str  # relative to the place; send it back as the run's `folder`
    date: str | None  # the date this folder stands for, if its name holds one


class PlaceListing(BaseModel):
    place: str
    path: str
    folders: list[FolderOut]
    invoice_files: int  # PDF / image files directly in this folder


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/places")
def get_places(
    session: SessionDep, settings: SettingsDep, client_id: str | None = None
) -> list[PlaceOut]:
    """The client's own folder (if it has one), then this Mac and the shared drives."""
    return [
        PlaceOut(key=p.key, label=p.label, path=str(p.path))
        for p in list_places(settings, _client(session, client_id))
    ]


@router.get("")
def get_folder(
    session: SessionDep,
    settings: SettingsDep,
    place: str,
    path: Annotated[str, Query()] = "",
    client_id: str | None = None,
) -> PlaceListing:
    try:
        found = find_place(settings, _client(session, client_id), place)
        listing = browse(found, path)
    except InboxError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    today = settings.business_today()
    prefix = f"{path.strip('/')}/" if path.strip("/") else ""
    folders = []
    for name in listing.folders:
        relative = f"{prefix}{name}"
        when = folder_date_from_path(relative, today=today)
        folders.append(FolderOut(name=name, path=relative, date=when.isoformat() if when else None))
    return PlaceListing(
        place=place, path=path, folders=folders, invoice_files=listing.invoice_files
    )


def _client(session: Session, client_id: str | None) -> Client | None:
    if client_id is None:
        return None
    client = get_client(session, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    return client
