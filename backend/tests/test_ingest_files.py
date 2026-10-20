from datetime import date
from pathlib import Path

import pytest

from app.domain.enums import Direction
from app.pipeline import file_types
from app.pipeline.ingest import list_candidate_files, run_folder


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


def test_run_folder_layout() -> None:
    folder = run_folder(Path("/inbox"), Direction.PURCHASE, date(2026, 10, 20))

    assert folder == Path("/inbox/Purchase/2026-10-20")


def test_listing_skips_hidden_temp_subfolders_and_symlinks(tmp_path: Path) -> None:
    for name in ("b.pdf", "a.jpg", ".DS_Store", "~$draft.pdf", ".hidden.pdf"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.pdf").write_bytes(b"x")
    (tmp_path / "link.pdf").symlink_to(tmp_path / "b.pdf")

    assert [p.name for p in list_candidate_files(tmp_path)] == ["a.jpg", "b.pdf"]
