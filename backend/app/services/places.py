"""Places to run from (decision 35): where the Run screen may browse and pick a folder.

The allowed places come from settings (this Mac's home folder and the shared drives by
default), plus the client's own folder when it has one. Every path a user gives is relative to
a place and must stay inside it (`resolve_inside`): the screen can never reach system folders,
and links pointing out of a place are refused. Folders are only ever read.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.models import Client
from app.pipeline.folders import InboxError, list_subfolders, resolve_inside

CLIENT_PLACE = "client"
# What the Run screen counts as an invoice file; ingest still checks the content itself.
INVOICE_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})


@dataclass(frozen=True, slots=True)
class Place:
    key: str  # sent back with the run: "client", or "p0", "p1", ... for the settings places
    label: str
    path: Path


@dataclass(frozen=True, slots=True)
class Listing:
    folders: list[str]  # names of the folders directly inside
    invoice_files: int  # invoice files directly inside (sub-folders are run too, not counted)


def list_places(settings: Settings, client: Client | None = None) -> list[Place]:
    """The client's own folder first (if any), then the allowed places that exist now."""
    places = []
    if client is not None and client.inbox_path and Path(client.inbox_path).is_dir():
        places.append(Place(CLIENT_PLACE, f"{client.name} folder", Path(client.inbox_path)))
    for index, path in enumerate(settings.folder_places):
        if path.is_dir():
            places.append(Place(f"p{index}", _label(path), path))
    return places


def find_place(settings: Settings, client: Client | None, key: str) -> Place:
    for place in list_places(settings, client):
        if place.key == key:
            return place
    raise InboxError(f"unknown or unreachable place: {key!r}")


def browse(place: Place, relative: str = "") -> Listing:
    folder = resolve_inside(place.path, relative)
    if not folder.is_dir():
        raise InboxError(f"folder not found: {relative or place.label}")
    return Listing(list_subfolders(folder), _count_invoice_files(folder))


def _count_invoice_files(folder: Path) -> int:
    try:
        return sum(
            1
            for entry in os.scandir(folder)
            if entry.is_file(follow_symlinks=False)
            and not entry.name.startswith((".", "~$"))
            and Path(entry.name).suffix.lower() in INVOICE_EXTENSIONS
        )
    except OSError as exc:
        raise InboxError(f"cannot read the folder ({type(exc).__name__})") from exc


def _label(path: Path) -> str:
    if path == Path.home():
        return "This Mac"
    if path == Path("/Volumes"):
        return "Shared drives"
    return path.name or str(path)
