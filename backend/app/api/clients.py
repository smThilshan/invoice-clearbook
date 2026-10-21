"""Clients (restaurants) and their folders, for the Run screen's pickers (decision 25).

Read only; clients are added with `make add-client` until the admin screen exists (behind
login, Phase 4). No login yet: the API listens on localhost only.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.pipeline.folders import InboxError, folder_date_from_path
from app.services.clients import browse, get_client, list_clients

router = APIRouter(prefix="/clients", tags=["clients"])


class ClientOut(BaseModel):
    client_id: str
    name: str


class Folder(BaseModel):
    name: str
    path: str  # relative to the client's folder; send it back as the run's `folder`
    date: str | None  # the date this folder stands for, if its name holds one


class FolderListing(BaseModel):
    path: str
    folders: list[Folder]


@router.get("")
def get_clients(session: Annotated[Session, Depends(get_session)]) -> list[ClientOut]:
    return [ClientOut(client_id=c.client_id, name=c.name) for c in list_clients(session)]


@router.get("/{client_id}/folders")
def get_folders(
    client_id: str,
    session: Annotated[Session, Depends(get_session)],
    path: Annotated[str, Query(description="Folder inside the client's folder")] = "",
) -> FolderListing:
    client = get_client(session, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    try:
        names = browse(client, path)
    except InboxError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    base = path.strip("/")
    folders = []
    for name in names:
        relative = f"{base}/{name}" if base else name
        found = folder_date_from_path(relative)
        folders.append(Folder(name=name, path=relative, date=found.isoformat() if found else None))
    return FolderListing(path=base, folders=folders)
