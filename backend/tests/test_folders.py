from datetime import date
from pathlib import Path

import pytest

from app.pipeline import file_types
from app.pipeline.folders import (
    InboxError,
    folder_date_from_path,
    list_files,
    list_subfolders,
    relative_to_root,
    resolve_inside,
)

# --- file types (content, not name) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"%PDF-1.7\n%\xe2\xe3", file_types.PDF),
        (b"\x00\x00junk before signature %PDF-1.4", file_types.PDF),
        (b"\x89PNG\r\n\x1a\n\x00\x00", file_types.PNG),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", file_types.JPEG),
        (b"\x00\x00\x00\x18ftypheic", None),  # iPhone HEIC: unsupported for now
        (b"PK\x03\x04", None),  # zip / docx / xlsx
        (b"", None),
    ],
)
def test_detect_by_content(head: bytes, expected: file_types.FileType | None) -> None:
    assert file_types.detect(head) == expected


def test_detect_ignores_misleading_content_after_head() -> None:
    assert file_types.detect(b"x" * file_types.HEAD_BYTES + b"%PDF-1.7") is None


# --- paths stay inside the client's folder --------------------------------------------------


@pytest.fixture
def client_folder(tmp_path: Path) -> Path:
    root = tmp_path / "Cafe Al Noor"
    (root / "October 2026" / "21").mkdir(parents=True)
    (root / "October 2026" / "22").mkdir()
    (root / "October 2026" / ".hidden").mkdir()
    (root / "October 2026" / "21" / "inv 1.pdf").write_bytes(b"x")
    (root / "October 2026" / "21" / ".DS_Store").write_bytes(b"x")
    (root / "October 2026" / "21" / "~$draft.xlsx").write_bytes(b"x")
    (root / "October 2026" / "22" / "inv 2.jpg").write_bytes(b"x")
    (root / "October 2026" / ".hidden" / "secret.pdf").write_bytes(b"x")
    (root / "top.pdf").write_bytes(b"x")
    return root


@pytest.mark.parametrize("relative", ["", "October 2026", "October 2026/21", " October 2026/ "])
def test_paths_inside_are_resolved(client_folder: Path, relative: str) -> None:
    path = resolve_inside(client_folder, relative)

    assert path.is_relative_to(client_folder.resolve())


@pytest.mark.parametrize(
    "relative", ["../", "../Other Restaurant", "October 2026/../../x", "/etc", "\\Windows"]
)
def test_paths_escaping_the_client_folder_are_refused(client_folder: Path, relative: str) -> None:
    with pytest.raises(InboxError, match="not a path inside"):
        resolve_inside(client_folder, relative)


def test_symlink_pointing_outside_is_refused(client_folder: Path, tmp_path: Path) -> None:
    other = tmp_path / "Other Restaurant"
    other.mkdir()
    (client_folder / "sneaky").symlink_to(other)

    with pytest.raises(InboxError, match="not a path inside"):
        resolve_inside(client_folder, "sneaky")


# --- listing --------------------------------------------------------------------------------


def test_list_files_includes_subfolders_but_skips_hidden_temp_and_symlinks(
    client_folder: Path,
) -> None:
    (client_folder / "October 2026" / "21" / "link.pdf").symlink_to(client_folder / "top.pdf")

    files = list_files(client_folder / "October 2026")

    assert [relative_to_root(client_folder, f) for f in files] == [
        "October 2026/21/inv 1.pdf",
        "October 2026/22/inv 2.jpg",
    ]


def test_symlinked_folders_are_not_entered(client_folder: Path, tmp_path: Path) -> None:
    other = tmp_path / "Other Restaurant"
    other.mkdir()
    (other / "theirs.pdf").write_bytes(b"x")
    (client_folder / "October 2026" / "linked").symlink_to(other)

    names = [f.name for f in list_files(client_folder)]

    assert "theirs.pdf" not in names


def test_list_subfolders_for_the_folder_picker(client_folder: Path) -> None:
    assert list_subfolders(client_folder) == ["October 2026"]
    assert list_subfolders(client_folder / "October 2026") == ["21", "22"]


def test_unreadable_folder_is_an_inbox_error(tmp_path: Path) -> None:
    with pytest.raises(InboxError, match="cannot read"):
        list_subfolders(tmp_path / "not-mounted")


# --- folder dates (decision 25) -------------------------------------------------------------

TODAY = date(2026, 10, 21)


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("October 2026/21", date(2026, 10, 21)),  # month folder, day folder
        ("Oct-2026/5", date(2026, 10, 5)),
        ("2026-10/21", date(2026, 10, 21)),
        ("10-2026/03", date(2026, 10, 3)),
        ("2026-10-21", date(2026, 10, 21)),  # a full date in one name
        ("21-10-2026", date(2026, 10, 21)),
        ("Purchase/2026-10-21", date(2026, 10, 21)),
        ("October 2026", date(2026, 10, 31)),  # a whole month: the month end
        ("February 2028", date(2028, 2, 29)),  # leap year
        ("February 2026/30", date(2026, 2, 28)),  # impossible day: the month end
        ("September 2026/Week 2", date(2026, 9, 30)),
    ],
)
def test_folder_date_from_path(relative: str, expected: date) -> None:
    assert folder_date_from_path(relative, today=TODAY) == expected


@pytest.mark.parametrize(
    "relative",
    ["", "Bills", "Supplier invoices/Misc", "02-09-2026", "Marketing", "Marketing 2026",
     "Mayfair/Invoices", "June 31"],
)  # fmt: skip
def test_no_or_ambiguous_date_in_the_name_is_none(relative: str) -> None:
    # 02-09-2026: 2 Sep or 9 Feb? never guessed. "Marketing" is not March.
    assert folder_date_from_path(relative, today=TODAY) is None


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("May/May 1", date(2026, 5, 1)),  # ROAST: month folder, day folders, no year
        ("May/1", date(2026, 5, 1)),
        ("May", date(2026, 5, 31)),
        ("May 1", date(2026, 5, 1)),
        ("1 May", date(2026, 5, 1)),
        ("May-01", date(2026, 5, 1)),
        ("May 1st", date(2026, 5, 1)),
        ("August/01st of August", date(2026, 8, 1)),  # Al Shahama's folders
        ("1 of May", date(2026, 5, 1)),
        ("september/sept 3", date(2026, 9, 3)),
        ("October", date(2026, 10, 31)),  # this month: its end, like "October 2026"
        ("December/December 30", date(2025, 12, 30)),  # not reached yet this year: last year's
        ("November", date(2025, 11, 30)),
        ("May 2025/May 1", date(2025, 5, 1)),  # a year on the month folder wins
    ],
)
def test_folder_names_without_a_year_take_the_most_recent_one(
    relative: str, expected: date
) -> None:
    assert folder_date_from_path(relative, today=TODAY) == expected


def test_most_recent_year_turns_over_at_new_year() -> None:
    assert folder_date_from_path("December/December 30", today=date(2027, 1, 3)) == date(
        2026, 12, 30
    )
    assert folder_date_from_path("January/January 2", today=date(2027, 1, 3)) == date(2027, 1, 2)
