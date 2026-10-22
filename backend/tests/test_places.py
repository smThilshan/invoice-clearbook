"""Places to run from (decision 35): only folders inside the allowed places are ever read."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.models import Client
from app.pipeline.folders import InboxError
from app.services.places import CLIENT_PLACE, browse, find_place, list_places


@pytest.fixture
def places(tmp_path: Path) -> Settings:
    mac, shared = tmp_path / "home", tmp_path / "Volumes"
    (mac / "Invoices" / "May 5").mkdir(parents=True)
    (mac / "Invoices" / "a.pdf").write_bytes(b"%PDF")
    (mac / "Invoices" / "photo.JPG").write_bytes(b"x")
    (mac / "Invoices" / "notes.txt").write_text("x")
    (mac / "Invoices" / ".hidden.pdf").write_bytes(b"%PDF")
    shared.mkdir()
    return Settings(_env_file=None, folder_places=[mac, shared, tmp_path / "unplugged"])


def client(folder: Path | None) -> Client:
    return Client(client_id="cafe", name="Cafe", inbox_path=str(folder) if folder else None)


def test_existing_places_are_offered_in_order(places: Settings) -> None:
    assert [p.key for p in list_places(places)] == ["p0", "p1"]  # unplugged one left out


def test_the_clients_own_folder_comes_first(places: Settings, tmp_path: Path) -> None:
    own = list_places(places, client(tmp_path / "home" / "Invoices"))[0]

    assert (own.key, own.label) == (CLIENT_PLACE, "Cafe folder")


def test_a_client_without_a_folder_gets_the_allowed_places(places: Settings) -> None:
    assert [p.key for p in list_places(places, client(None))] == ["p0", "p1"]


def test_a_folder_lists_sub_folders_and_counts_invoice_files(places: Settings) -> None:
    listing = browse(find_place(places, None, "p0"), "Invoices")

    assert listing.folders == ["May 5"]
    assert listing.invoice_files == 2  # pdf + jpg; not the text file or the hidden one


@pytest.mark.parametrize("path", ["../..", "/etc", "Invoices/../../.."])
def test_paths_cannot_leave_the_place(places: Settings, path: str) -> None:
    with pytest.raises(InboxError):
        browse(find_place(places, None, "p0"), path)


def test_a_link_pointing_out_of_the_place_is_refused(places: Settings, tmp_path: Path) -> None:
    (tmp_path / "home" / "escape").symlink_to(tmp_path)

    with pytest.raises(InboxError):
        browse(find_place(places, None, "p0"), "escape")


def test_unknown_place_is_refused(places: Settings) -> None:
    with pytest.raises(InboxError):
        find_place(places, None, "p9")
