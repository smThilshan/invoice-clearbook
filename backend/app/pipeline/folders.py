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
# No year (decision 31): "May", "May 1", "1 May", "May-01", "May 1st", "01st of May"
_MONTH_ONLY = re.compile(rf"^(?P<name>{_NAME})$")
_MONTH_DAY = re.compile(
    rf"^(?:(?P<name>{_NAME})[\s\-_.]*(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"|(?P<day2>\d{{1,2}})(?:st|nd|rd|th)?[\s\-_.]*(?:of[\s\-_.]+)?(?P<name2>{_NAME}))$",
    re.IGNORECASE,
)
# A range of days (decision 85): "MARCH 0 - 15", "April 1- 5", "January 11 - 20": its last day.
_MONTH_RANGE = re.compile(
    rf"^(?P<name>{_NAME})[\s_.]*(?P<first>\d{{1,2}})\s*-\s*(?P<last>\d{{1,2}})$"
)
_DAY = re.compile(r"^\d{1,2}$")


def folder_date_from_path(relative: str, *, today: date) -> date | None:
    """The date a folder path stands for (decisions 25, 31, 85):
    a full date in a folder name (2026-10-21) -> that day; a day folder inside a month folder
    (October 2026/21) -> that day; a month folder alone (October 2026) -> the month end; a
    range of days (March 1 - 15) -> its last day; nothing date-like, or folders naming
    different months (March 1 - 15/April 5) -> None. Without a year ("May/May 1", "May/1",
    "May") the year is the one that makes it the most recent such date up to `today` (the
    business day)."""
    year = month = None
    found: date | None = None
    for part in PurePosixPath(relative.replace("\\", "/")).parts:
        part = part.strip()
        month_year = _month_year(part)
        month_day = _month_day(part) or _month_range(part)
        month_only = _month_number(m["name"]) if (m := _MONTH_ONLY.match(part)) else None
        named = month_year[1] if month_year else month_day[0] if month_day else month_only
        if named is not None and month is not None and named != month:
            return None  # the folders disagree: no date is better than a wrong one
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
                if month is not None and reading.value.month != month:
                    return None
                found = reading.value
                year, month = found.year, found.month
    return found


def file_folder_date(
    relative_path: str, *, run_folder: str, run_date: date | None, today: date
) -> date | None:
    """The folder date of one file of a run (decision 85): the date its own folders stand for
    ("MARCH 1 - 15/March 11/gala.pdf" -> 11 March, though the run was started on the month
    folder), or the run's when it was typed for the run, not taken from the run's folder."""
    if run_date is not None and run_date != folder_date_from_path(run_folder, today=today):
        return run_date
    folders = str(PurePosixPath(relative_path.replace("\\", "/")).parent)
    return folder_date_from_path(folders, today=today) or run_date


_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def folder_year_known(relative_path: str) -> bool:
    """Whether a file's folders write the year ("October 2026", "2026-10-21"); False when
    the year of its folder date was assumed ("August/14th August")."""
    folders = PurePosixPath(relative_path.replace("\\", "/")).parent.parts
    return any(_YEAR.search(part) for part in folders)


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


def _month_range(part: str) -> tuple[int, int] | None:
    match = _MONTH_RANGE.match(part)
    if match is None:
        return None
    month, last = _month_number(match["name"]), int(match["last"])
    return (month, last) if month and int(match["first"]) <= last <= 31 else None


def _month_number(name: str) -> int | None:
    """A whole month name or its abbreviation; "Marketing" is not March."""
    return _MONTH_NAMES.get(name.upper())


_MONTH_NAMES = {
    **{name.upper(): n for n, name in enumerate(calendar.month_name) if name},
    **{name.upper(): n for n, name in enumerate(calendar.month_abbr) if name},
    "SEPT": 9,
    "FEBUARY": 2,  # a common misspelling, seen on the shared drive
}


def _hidden(name: str) -> bool:
    return name.startswith((".", "~$"))


def _raise(error: OSError) -> None:
    raise error
