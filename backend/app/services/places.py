"""Places to run from (decision 35): where the Run screen may browse and pick a folder.

The allowed places come from settings (this Mac's home folder and the shared drives by
default), plus the client's own folder when it has one. Every path a user gives is relative to
a place and must stay inside it (`resolve_inside`): the screen can never reach system folders,
and links pointing out of a place are refused. Folders are only ever read.
"""

import os
import time
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
    invoice_files: int  # invoice files directly inside
    # Task 4.17: invoice files a run of this folder would take in (sub-folders included), and
    # per sub-folder. None = too many entries to count quickly (e.g. a whole home folder).
    total_files: int | None
    folder_files: dict[str, int | None]


# Counting stops after this many folder entries per folder, and after this long in all: a
# listing of a big place (the home folder) must stay quick.
COUNT_ENTRY_BUDGET = 3000
COUNT_SECONDS = 1.5


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
    names = list_subfolders(folder)
    deadline = time.monotonic() + COUNT_SECONDS
    per_folder = {name: count_with_subfolders(folder / name, deadline) for name in names}
    direct = _count_invoice_files(folder)
    totals = list(per_folder.values())
    total = None if None in totals else direct + sum(t for t in totals if t is not None)
    return Listing(names, direct, total, per_folder)


def count_with_subfolders(folder: Path, deadline: float) -> int | None:
    """Invoice files in `folder` and below (hidden folders and links skipped, like a run);
    None when it holds too many entries to count before the deadline."""
    count = entries = 0
    try:
        for directory, subdirs, names in os.walk(folder, followlinks=False):
            subdirs[:] = [d for d in subdirs if not d.startswith((".", "~$"))]
            entries += len(subdirs) + len(names)
            if entries > COUNT_ENTRY_BUDGET or time.monotonic() > deadline:
                return None
            count += sum(
                1
                for name in names
                if not name.startswith((".", "~$"))
                and Path(name).suffix.lower() in INVOICE_EXTENSIONS
                and not Path(directory, name).is_symlink()
            )
    except OSError:
        return None
    return count


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
