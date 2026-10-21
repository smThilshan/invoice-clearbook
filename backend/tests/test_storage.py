import uuid
from pathlib import Path

import pytest

from app.adapters.storage import InvalidKeyError, NotFoundError, get_storage
from app.adapters.storage.keys import ocr_key, original_key, page_image_key
from app.adapters.storage.local import LocalStorage
from app.core.config import REPO_ROOT, Settings

SHA = "ab" * 32
DOC = uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


def leftovers(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.name.endswith(".tmp")]


def test_put_then_get_round_trip_in_nested_folders(storage: LocalStorage, tmp_path: Path) -> None:
    storage.put("missan/pages/doc-1/page-001.png", b"\x89PNG data")

    assert storage.get("missan/pages/doc-1/page-001.png") == b"\x89PNG data"
    assert storage.exists("missan/pages/doc-1/page-001.png")
    assert (tmp_path / "store/missan/pages/doc-1/page-001.png").is_file()


def test_put_replaces_existing_object(storage: LocalStorage) -> None:
    storage.put("missan/ocr/x/page-001.json", b"old")
    storage.put("missan/ocr/x/page-001.json", b"new")

    assert storage.get("missan/ocr/x/page-001.json") == b"new"


def test_put_file_streams_a_copy(storage: LocalStorage, tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"%PDF-1.7 " + b"x" * 3_000_000)  # larger than one copy chunk

    storage.put_file("missan/originals/abc.pdf", source)

    assert storage.get("missan/originals/abc.pdf") == source.read_bytes()
    assert source.exists()  # copied, never moved: the inbox belongs to the accountants


def test_no_temp_files_left_behind(storage: LocalStorage, tmp_path: Path) -> None:
    storage.put("missan/a/b.json", b"{}")

    assert leftovers(tmp_path) == []


def test_failed_write_leaves_neither_object_nor_temp_file(
    storage: LocalStorage, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist.pdf"

    with pytest.raises(FileNotFoundError):
        storage.put_file("missan/originals/abc.pdf", missing)

    assert not storage.exists("missan/originals/abc.pdf")
    assert leftovers(tmp_path) == []


def test_missing_key_raises_not_found(storage: LocalStorage) -> None:
    assert not storage.exists("missan/nothing.png")
    with pytest.raises(NotFoundError, match=r"missan/nothing\.png"):
        storage.get("missan/nothing.png")


@pytest.mark.parametrize(
    "key",
    ["", "/etc/passwd", "../outside", "missan/../../outside", "missan//x", "missan/./x",
     "missan\\x", "missan/.hidden", "missan/x/"],
)  # fmt: skip
def test_unsafe_keys_rejected(storage: LocalStorage, key: str) -> None:
    with pytest.raises(InvalidKeyError):
        storage.put(key, b"x")


def test_symlinked_folder_cannot_escape_the_store(storage: LocalStorage, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "store").mkdir()
    (tmp_path / "store/missan").symlink_to(outside)

    with pytest.raises(InvalidKeyError, match="escapes"):
        storage.put("missan/x.json", b"{}")
    assert list(outside.iterdir()) == []


def test_factory_uses_store_dir_from_settings(tmp_path: Path) -> None:
    storage = get_storage(Settings(_env_file=None, store_dir=tmp_path / "s"))
    storage.put("missan/x.json", b"{}")

    assert (tmp_path / "s/missan/x.json").is_file()


def test_relative_dirs_resolve_against_repo_root() -> None:
    settings = Settings(_env_file=None, store_dir="data/store")

    assert settings.store_dir == REPO_ROOT / "data/store"


# --- keys ---------------------------------------------------------------------------------


def test_original_key_is_content_addressed() -> None:
    assert original_key("missan", SHA, ".PDF") == f"missan/originals/{SHA}.pdf"


@pytest.mark.parametrize(("sha", "ext"), [("abc", ".pdf"), (SHA.upper(), ".pdf"), (SHA, "pdf")])
def test_original_key_rejects_bad_parts(sha: str, ext: str) -> None:
    with pytest.raises(ValueError, match=r"sha256|extension"):
        original_key("missan", sha, ext)


def test_page_keys_are_numbered_from_one() -> None:
    assert page_image_key("missan", DOC, 1) == f"missan/pages/{DOC}/page-001.png"
    assert ocr_key("missan", DOC, 12) == f"missan/ocr/{DOC}/page-012.json"
    with pytest.raises(ValueError, match="from 1"):
        page_image_key("missan", DOC, 0)


def test_keys_reject_unsafe_client_id() -> None:
    with pytest.raises(InvalidKeyError):
        page_image_key("../evil", DOC, 1)
