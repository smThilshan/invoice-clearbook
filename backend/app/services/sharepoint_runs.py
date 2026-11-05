"""Running a SharePoint folder (decision 83), sub-folders included.

Starting makes the run at once, with an empty folder of its own under `upload_dir/<client>/`,
and remembers where the folder is on SharePoint (by id: a rename does not lose it). The worker
then fetches the folder's invoice files into it, keeping their sub-folders, before the usual
scan: from there it is the same as any folder (decision 35), so the same ingest copies each
invoice into the store and the same duplicate check skips one seen before. Nothing is ever
written to SharePoint, and the folder there stays as it is.

Fetching is safe to repeat (a stopped run resumed, a worker restarted): a file already fetched
whole is kept; a file is written under a hidden name and renamed only when complete, so the scan
never takes half a file. Progress (`fetched` of `fetch_total`) is committed as it goes.
"""

import uuid
from collections import deque
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Literal

from sqlalchemy.orm import Session

from app.adapters.sharepoint.graph import GraphClient, Item
from app.core.config import Settings
from app.models import Client, Run, RunStatus
from app.pipeline.folders import folder_date_from_path, resolve_inside
from app.services import sharepoint
from app.services.runs import FROM_FOLDER, start_run
from app.services.sharepoint import MICROSOFT, Microsoft, SharePointError
from app.services.uploads import clean_path

SYSTEM = "sharepoint"


def start_sharepoint_run(
    session: Session,
    settings: Settings,
    *,
    client: Client,
    drive_id: str,
    item_id: str,
    folder_date: date | Literal["from_folder"] | None = FROM_FOLDER,
    created_by: str,
    microsoft: Microsoft = MICROSOFT,
) -> Run:
    """Create the run for a SharePoint folder; the caller commits, then queues the scan, which
    fetches first. The date is the user's, by default read from the folder's path on SharePoint
    ("…/April/April 11": 11 April)."""
    item = sharepoint.call(
        session, settings, lambda graph: graph.item(drive_id, item_id), microsoft
    )
    if not item.is_folder:
        raise SharePointError(f"{item.name} is a file, not a folder")
    name = clean_path(item.name)
    folder = name.name if name is not None else "SharePoint"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    root = settings.upload_dir / client.client_id / f"{stamp}-{uuid.uuid4().hex[:8]}"
    (root / folder).mkdir(parents=True)
    today = settings.business_today()
    run = start_run(
        session,
        client=client,
        root=root,
        folder=folder,
        folder_date=(
            folder_date_from_path(item.path or item.name, today=today)
            if folder_date == FROM_FOLDER
            else folder_date
        ),
        today=today,
        created_by=created_by,
    )
    run.remote = {"system": SYSTEM, "drive_id": drive_id, "item_id": item_id, "path": item.path}
    run.fetched = 0
    session.flush()
    return run


def needs_fetch(run: Run) -> bool:
    return run.remote is not None and run.files is None


def fetch(session: Session, settings: Settings, run: Run, microsoft: Microsoft = MICROSOFT) -> bool:
    """Fetch the run's SharePoint folder into its own folder. True when every invoice file is
    here; False when the run was stopped meanwhile (Resume fetches the rest). Raises
    SharePointError, with the reason for the screen, when it cannot be fetched. Commits as it
    goes, so the Run screen sees the progress."""
    if run.remote is None or run.root_path is None:
        raise SharePointError("this run has no SharePoint folder")
    drive_id, item_id = run.remote["drive_id"], run.remote["item_id"]
    files = sharepoint.call(
        session, settings, lambda graph: _invoice_files(graph, drive_id, item_id), microsoft
    )
    _check_limits(settings, files)
    run.fetch_total, run.fetched = len(files), 0
    session.commit()

    folder = resolve_inside(Path(run.root_path), run.folder)
    for inside, item in files:
        session.refresh(run)
        if run.status is RunStatus.STOPPED:
            return False
        target = resolve_inside(folder, inside.as_posix())
        if not (target.is_file() and target.stat().st_size == item.size):
            sharepoint.call(
                session, settings, partial(_download, item=item, target=target), microsoft
            )
        run.fetched = (run.fetched or 0) + 1
        session.commit()
    return True


def _invoice_files(
    graph: GraphClient, drive_id: str, item_id: str
) -> list[tuple[PurePosixPath, Item]]:
    """Every invoice file in the folder and below it, with its path inside the folder. Hidden
    folders are skipped, as a scan of a folder here would."""
    found: list[tuple[PurePosixPath, Item]] = []
    waiting: deque[tuple[PurePosixPath, str]] = deque([(PurePosixPath(), item_id)])
    while waiting:
        inside, folder_id = waiting.popleft()
        for item in graph.children(drive_id, folder_id):
            if sharepoint.hidden(item.name):
                continue
            name = clean_path(item.name)
            if name is None:
                continue
            if item.is_folder:
                waiting.append((inside / name, item.item_id))
            elif sharepoint.is_invoice(item):
                found.append((inside / name, item))
    return sorted(found, key=lambda pair: pair[0].as_posix())


def _check_limits(settings: Settings, files: list[tuple[PurePosixPath, Item]]) -> None:
    if not files:
        raise SharePointError("no invoice files (PDF, PNG or JPEG) in this SharePoint folder")
    if len(files) > settings.max_upload_files:
        raise SharePointError(
            f"{len(files)} invoice files in this folder; at most {settings.max_upload_files} "
            "in one run: run a folder inside it"
        )
    if sum(item.size for _, item in files) > settings.max_upload_mb * 1024 * 1024:
        raise SharePointError(
            f"more than {settings.max_upload_mb} MB of invoices in this folder: "
            "run a folder inside it"
        )


def _download(graph: GraphClient, item: Item, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")  # hidden: a scan never takes it
    try:
        with partial.open("wb") as out:
            graph.download(item.drive_id, item.item_id, out)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
