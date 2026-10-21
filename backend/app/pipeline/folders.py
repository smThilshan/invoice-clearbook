"""A client's folder on the shared drive (decision 25): safe paths, listing, folder dates.

Every path a user or a job gives is relative to the client's folder and must stay inside it:
absolute paths, "..", and symlinks pointing out are refused (InboxError). The folder is only
ever read, never changed.
"""

import calendar
import os
import re
from datetime import date
from pathlib import Path, PurePosixPath

from app.pipeline.verify.normalize import parse_date


class InboxError(Exception):
    """A folder or file cannot be used: outside the client's folder, missing, or unreadable."""


def resolve_inside(root: Path, relative: str) -> Path:
    """`relative` (from a request or a job) inside `root`, as a real path."""
    parts = PurePosixPath(relative.strip().replace("\\", "/")).parts
    if relative.strip().startswith(("/", "\\")) or ".." in parts:
        raise InboxError(f"not a path inside the client folder: {relative!r}")
    real_root = root.resolve()
    path = real_root.joinpath(*parts).resolve()  # resolve() follows symlinks...
    if not path.is_relative_to(real_root):  # ...so a link pointing out is caught here
        raise InboxError(f"not a path inside the client folder: {relative!r}")
    return path


def relative_to_root(root: Path, path: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()


def list_files(folder: Path) -> list[Path]:
    """Files in `folder` and all its sub-folders, sorted; hidden/temp files and symlinks
    skipped, hidden folders not entered."""
    found: list[Path] = []
    try:
        walker = os.walk(folder, followlinks=False, onerror=_raise)
        for directory, subdirs, names in walker:
            subdirs[:] = sorted(d for d in subdirs if not _hidden(d))
            for name in names:
                path = Path(directory, name)
                if not _hidden(name) and path.is_file() and not path.is_symlink():
                    found.append(path)
    except OSError as exc:
        raise InboxError(f"cannot read the folder ({type(exc).__name__})") from exc
    return sorted(found)


def list_subfolders(folder: Path) -> list[str]:
    """Names of the folders directly inside `folder`, for the Run screen's folder picker."""
    try:
        return sorted(
            entry.name
            for entry in os.scandir(folder)
            if entry.is_dir(follow_symlinks=False) and not _hidden(entry.name)
        )
    except OSError as exc:
        raise InboxError(f"cannot read the folder ({type(exc).__name__})") from exc


_MONTH_YEAR = re.compile(
    r"^(?:(?P<name>[A-Za-z]{3,9})[\s\-_.]*(?P<year1>\d{4})"  # October 2026, Oct-2026
    r"|(?P<year2>\d{4})[\s\-_.](?P<num2>\d{1,2})"  # 2026-10
    r"|(?P<num3>\d{1,2})[\s\-_.](?P<year3>\d{4}))$"  # 10-2026
)
_DAY = re.compile(r"^\d{1,2}$")


def folder_date_from_path(relative: str) -> date | None:
    """The date a folder path stands for (decision 25):
    a full date in a folder name (2026-10-21) -> that day; a day folder inside a month folder
    (October 2026/21) -> that day; a month folder alone (October 2026) -> the month end;
    nothing date-like -> None."""
    year = month = None
    found: date | None = None
    for part in PurePosixPath(relative.replace("\\", "/")).parts:
        month_year = _month_year(part)
        if month_year is not None:
            year, month = month_year
            found = date(year, month, calendar.monthrange(year, month)[1])  # month end
        elif _DAY.match(part) and year is not None and month is not None:
            day = int(part)
            if 1 <= day <= calendar.monthrange(year, month)[1]:
                found = date(year, month, day)
        else:
            reading = parse_date(part)
            if reading is not None and not reading.ambiguous:
                found = reading.value
                year, month = found.year, found.month
    return found


def _month_year(part: str) -> tuple[int, int] | None:
    match = _MONTH_YEAR.match(part.strip())
    if match is None:
        return None
    if match["name"]:
        number = _MONTH_NAMES.get(match["name"][:3].upper())
        return (int(match["year1"]), number) if number else None
    year, month = (
        (int(match["year2"]), int(match["num2"]))
        if match["year2"]
        else (int(match["year3"]), int(match["num3"]))
    )
    return (year, month) if 1 <= month <= 12 else None


_MONTH_NAMES = {name.upper(): n for n, name in enumerate(calendar.month_abbr) if name}


def _hidden(name: str) -> bool:
    return name.startswith((".", "~$"))


def _raise(error: OSError) -> None:
    raise error
