"""A folder chosen on the accountant's own computer (decision 78).

The browser sends the folder's invoice files with their paths inside it ("October 2026/21/a.pdf").
They are saved in a new folder of their own under `upload_dir/<client>/`, and that folder is
then run like any folder (decision 35): the same ingest copies each invoice into the store, the
same duplicate check skips a file seen before, and the folder date is read from the chosen
folder's name. Only invoice files are kept; paths are cleaned so nothing is written outside the
upload's folder.
"""

import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Client, Run
from app.pipeline.folders import resolve_inside
from app.services.places import INVOICE_EXTENSIONS
from app.services.runs import start_run


class UploadError(ValueError):
    """The upload is refused as a whole (too many files, too large, nothing to run)."""


@dataclass(frozen=True, slots=True)
class UploadedFile:
    path: str  # inside the chosen folder, as the browser sent it
    content: BinaryIO
    size: int


@dataclass(frozen=True, slots=True)
class UploadResult:
    run: Run
    files: int  # invoice files saved and run
    skipped: list[str]  # not invoice files, or unsafe or repeated paths


def start_upload_run(
    session: Session,
    settings: Settings,
    *,
    client: Client,
    files: Sequence[UploadedFile],
    folder_date: date | None,
    created_by: str,
) -> UploadResult:
    """Save the invoice files, then start their run; the caller commits and queues the scan."""
    if len(files) > settings.max_upload_files:
        raise UploadError(f"at most {settings.max_upload_files} files in one upload")
    if sum(file.size for file in files) > settings.max_upload_mb * 1024 * 1024:
        raise UploadError(f"at most {settings.max_upload_mb} MB in one upload")

    kept: dict[PurePosixPath, UploadedFile] = {}
    skipped: list[str] = []
    for file in files:
        path = clean_path(file.path)
        if path is None or path in kept or path.suffix.lower() not in INVOICE_EXTENSIONS:
            skipped.append(file.path)
        else:
            kept[path] = file
    if not kept:
        raise UploadError("no invoice files (PDF, PNG or JPEG) in this upload")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    root = settings.upload_dir / client.client_id / f"{stamp}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True)
    try:
        for path, file in kept.items():
            target = resolve_inside(root, path.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as out:
                shutil.copyfileobj(file.content, out)
        run = start_run(session, client=client, root=root, folder=_chosen_folder(kept),
                        folder_date=folder_date, today=settings.business_today(),
                        created_by=created_by)  # fmt: skip
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)  # nothing half-saved stays behind
        raise
    return UploadResult(run, len(kept), skipped)


def clean_path(sent: str) -> PurePosixPath | None:
    """The path inside the chosen folder, or None when it cannot be saved safely: absolute,
    climbing out (".."), empty, or a hidden or temporary file as a folder scan skips them."""
    text = sent.replace("\\", "/")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if text.startswith("/") or not parts or ".." in parts:
        return None
    if any(part.startswith((".", "~$")) or "\x00" in part for part in parts):
        return None
    return PurePosixPath(*parts)


def _chosen_folder(paths: dict[PurePosixPath, UploadedFile]) -> str:
    """The folder the user chose: the first part every path shares ("October 2026"), so the run
    shows its name and its date is read from it; "" for files chosen one by one."""
    tops = {path.parts[0] for path in paths if len(path.parts) > 1}
    if len(tops) == 1 and all(len(path.parts) > 1 for path in paths):
        return tops.pop()
    return ""
