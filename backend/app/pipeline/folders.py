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


_NAME = r"[A-Za-z]{3,9}"
_MONTH_YEAR = re.compile(
    rf"^(?:(?P<name>{_NAME})[\s\-_.]*(?P<year1>\d{{4}})"  # October 2026, Oct-2026
    r"|(?P<year2>\d{4})[\s\-_.](?P<num2>\d{1,2})"  # 2026-10
    r"|(?P<num3>\d{1,2})[\s\-_.](?P<year3>\d{4}))$"  # 10-2026
)
# No year (decision 31): "May", "May 1", "1 May", "May-01", "May 1st"
_MONTH_ONLY = re.compile(rf"^(?P<name>{_NAME})$")
_MONTH_DAY = re.compile(
    rf"^(?:(?P<name>{_NAME})[\s\-_.]*(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"|(?P<day2>\d{{1,2}})(?:st|nd|rd|th)?[\s\-_.]*(?P<name2>{_NAME}))$",
    re.IGNORECASE,
)
_DAY = re.compile(r"^\d{1,2}$")


def folder_date_from_path(relative: str, *, today: date) -> date | None:
    """The date a folder path stands for (decisions 25, 31):
    a full date in a folder name (2026-10-21) -> that day; a day folder inside a month folder
    (October 2026/21) -> that day; a month folder alone (October 2026) -> the month end;
    nothing date-like -> None. Without a year ("May/May 1", "May/1", "May") the year is the
    one that makes it the most recent such date up to `today` (the business day)."""
    year = month = None
    found: date | None = None
    for part in PurePosixPath(relative.replace("\\", "/")).parts:
        part = part.strip()
        month_year = _month_year(part)
        month_day = _month_day(part)
        month_only = _month_number(m["name"]) if (m := _MONTH_ONLY.match(part)) else None
        if month_year is not None:
            year, month = month_year
            found = _month_end(year, month)
        elif month_day is not None:
            day_month, day = month_day
            if year is None or day_month != month:  # "May 2026/May 1" keeps the parent's year
                year = _latest_year(day_month, day, today)
            month = day_month
            found = _day_in(year, month, day) or found
        elif month_only is not None:
            month = month_only
            year = _latest_year(month, 1, today)
            found = _month_end(year, month)
        elif _DAY.match(part) and year is not None and month is not None:
            found = _day_in(year, month, int(part)) or found
        else:
            reading = parse_date(part)
            if reading is not None and not reading.ambiguous:
                found = reading.value
                year, month = found.year, found.month
    return found


def _latest_year(month: int, day: int, today: date) -> int:
    """The year in which month/day was most recently reached, up to today."""
    return today.year if (month, day) <= (today.month, today.day) else today.year - 1


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _day_in(year: int, month: int, day: int) -> date | None:
    return date(year, month, day) if 1 <= day <= calendar.monthrange(year, month)[1] else None


def _month_year(part: str) -> tuple[int, int] | None:
    match = _MONTH_YEAR.match(part)
    if match is None:
        return None
    if match["name"]:
        number = _month_number(match["name"])
        return (int(match["year1"]), number) if number else None
    year, month = (
        (int(match["year2"]), int(match["num2"]))
        if match["year2"]
        else (int(match["year3"]), int(match["num3"]))
    )
    return (year, month) if 1 <= month <= 12 else None


def _month_day(part: str) -> tuple[int, int] | None:
    match = _MONTH_DAY.match(part)
    if match is None:
        return None
    month = _month_number(match["name"] or match["name2"])
    day = int(match["day"] or match["day2"])
    return (month, day) if month and 1 <= day <= 31 else None


def _month_number(name: str) -> int | None:
    """A whole month name or its abbreviation; "Marketing" is not March."""
    return _MONTH_NAMES.get(name.upper())


_MONTH_NAMES = {
    **{name.upper(): n for n, name in enumerate(calendar.month_name) if name},
    **{name.upper(): n for n, name in enumerate(calendar.month_abbr) if name},
    "SEPT": 9,
}


def _hidden(name: str) -> bool:
    return name.startswith((".", "~$"))


def _raise(error: OSError) -> None:
    raise error
