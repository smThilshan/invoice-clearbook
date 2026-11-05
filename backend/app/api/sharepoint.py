"""SharePoint, read only (decision 83): connect Missan's sign-in, then browse sites and folders.

The admin's browser follows /sharepoint/connect to Microsoft's sign-in, which sends it back to
/sharepoint/callback (the address registered in Entra ID), and that sends it on to the Workbench
with the outcome. Every user may browse and run: it only reads. Runs: POST /runs/sharepoint.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.sharepoint.graph import Item
from app.api.auth import AdminDep, require_admin
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.pipeline.folders import folder_date_from_path
from app.services import sharepoint
from app.services.sharepoint import MICROSOFT, Folder, Microsoft, SharePointError

router = APIRouter(prefix="/sharepoint", tags=["sharepoint"])
# Microsoft sends the browser back here from its own site: the signed `state` proves the
# sign-in started here, so this one route is open (like the accounting systems' return).
callback_router = APIRouter(prefix="/sharepoint", tags=["sharepoint"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_microsoft() -> Microsoft:
    """How Microsoft is reached (replaced in tests)."""
    return MICROSOFT


MicrosoftDep = Annotated[Microsoft, Depends(get_microsoft)]


class SharePointStatus(BaseModel):
    configured: bool  # the app's keys are in .env
    connected: bool
    needs_reconnect: bool  # Microsoft no longer accepts the sign-in: connect again
    reconnect_reason: str | None
    account_name: str | None  # whose SharePoint access the Workbench uses
    account_email: str | None
    connected_by: str | None
    connected_at: datetime | None


class SiteOut(BaseModel):
    site_id: str
    name: str
    web_url: str


class LibraryOut(BaseModel):
    drive_id: str
    name: str  # "Documents"


class SharePointFolderOut(BaseModel):
    drive_id: str
    item_id: str  # send both back to open it, or to run it
    name: str
    path: str  # inside its library; "" = the library itself
    date: date | None  # the date its name (with the folders above) stands for
    items: int | None  # entries directly inside


class SharePointListing(BaseModel):
    folder: SharePointFolderOut
    folders: list[SharePointFolderOut]
    invoice_files: int  # PDF / image files directly in this folder
    other_files: int  # left out of a run


@router.get("/status")
def get_status(
    session: SessionDep, settings: SettingsDep, microsoft: MicrosoftDep
) -> SharePointStatus:
    found = sharepoint.status(session, settings, microsoft)
    connection = found.connection
    return SharePointStatus(
        configured=found.configured,
        connected=connection is not None,
        needs_reconnect=bool(connection and connection.needs_reconnect),
        reconnect_reason=connection.reconnect_reason if connection else None,
        account_name=connection.account_name if connection else None,
        account_email=connection.account_email if connection else None,
        connected_by=connection.connected_by if connection else None,
        connected_at=connection.created_at if connection else None,
    )


@router.get("/connect")
def connect(
    settings: SettingsDep,
    microsoft: MicrosoftDep,
    user: AdminDep,
    back: Literal["clients", "run"] = "clients",
) -> RedirectResponse:
    """Start Microsoft's sign-in; afterwards the admin returns to `back`."""
    try:
        url = sharepoint.start(settings, user.email, back, microsoft)
    except SharePointError as exc:
        return _back(settings, back, sharepoint_error=str(exc))
    return RedirectResponse(url, status.HTTP_307_TEMPORARY_REDIRECT)


@callback_router.get("/callback")
def callback(
    session: SessionDep,
    settings: SettingsDep,
    microsoft: MicrosoftDep,
    state: str = "",
    code: Annotated[str, Query()] = "",
    error: str | None = None,
) -> RedirectResponse:
    back = sharepoint.return_page(settings, state)
    if error or not code:
        return _back(
            settings,
            back,
            sharepoint_error=f"SharePoint sign-in was not completed ({error or 'no code'})",
        )
    try:
        back, account = sharepoint.finish(session, settings, state, code, microsoft)
        session.commit()
    except SharePointError as exc:
        session.rollback()
        return _back(settings, back, sharepoint_error=str(exc))
    return _back(settings, back, sharepoint=account.name or account.email)


@router.post("/disconnect", dependencies=[Depends(require_admin)])
def disconnect(
    session: SessionDep, settings: SettingsDep, microsoft: MicrosoftDep
) -> SharePointStatus:
    sharepoint.disconnect(session)
    session.commit()
    return get_status(session, settings, microsoft)


@router.get("/sites")
def get_sites(session: SessionDep, settings: SettingsDep, microsoft: MicrosoftDep) -> list[SiteOut]:
    """The sites the connected account can open."""
    found = _answer(session, lambda: sharepoint.sites(session, settings, microsoft))
    return [SiteOut(site_id=s.site_id, name=s.name, web_url=s.web_url) for s in found]


@router.get("/sites/{site_id}/libraries")
def get_libraries(
    site_id: str, session: SessionDep, settings: SettingsDep, microsoft: MicrosoftDep
) -> list[LibraryOut]:
    found = _answer(session, lambda: sharepoint.libraries(session, settings, site_id, microsoft))
    return [LibraryOut(drive_id=lib.drive_id, name=lib.name) for lib in found]


@router.get("/folder")
def get_folder(
    drive_id: str,
    session: SessionDep,
    settings: SettingsDep,
    microsoft: MicrosoftDep,
    item_id: str = "root",
) -> SharePointListing:
    """What is in a folder ("root": the library itself)."""
    view = _answer(
        session, lambda: sharepoint.folder(session, settings, drive_id, item_id, microsoft)
    )
    return SharePointListing(
        folder=_folder_out(view.folder),
        folders=[_folder_out(f) for f in view.folders],
        invoice_files=view.invoice_files,
        other_files=view.other_files,
    )


@router.get("/link")
def get_link(
    url: str, session: SessionDep, settings: SettingsDep, microsoft: MicrosoftDep
) -> SharePointFolderOut:
    """The folder a pasted SharePoint address leads to."""
    item = _answer(session, lambda: sharepoint.linked_folder(session, settings, url, microsoft))
    return _folder_out(
        Folder(item, folder_date_from_path(item.path, today=settings.business_today()))
    )


def _answer[T](session: Session, ask: Callable[[], T]) -> T:
    """SharePoint's answer; a renewed sign-in is kept even when the question fails."""
    try:
        found = ask()
    except SharePointError as exc:
        session.commit()  # a renewed token, or the mark that a new sign-in is needed
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return found


def _folder_out(folder: Folder) -> SharePointFolderOut:
    item: Item = folder.item
    return SharePointFolderOut(
        drive_id=item.drive_id,
        item_id=item.item_id,
        name=item.name,
        path=item.path,
        date=folder.date,
        items=item.child_count,
    )


def _back(settings: Settings, page: str, **outcome: str) -> RedirectResponse:
    """Back to a Workbench page (only ever one of sharepoint.BACK_PAGES), saying what happened."""
    query = urlencode({key: value for key, value in outcome.items() if value})
    return RedirectResponse(f"{settings.workbench_url}/{page}?{query}", status.HTTP_303_SEE_OTHER)
