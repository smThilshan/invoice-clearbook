"""SharePoint is only ever read (decision 83): the sign-in asks for read-only access, and the
Graph client can only send GET, with the token only to Graph. Against a made-up Microsoft."""

import inspect
import io
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters.sharepoint import auth as auth_module
from app.adapters.sharepoint import graph as graph_module
from app.adapters.sharepoint.auth import MicrosoftAuth, SharePointAccessRevoked
from app.adapters.sharepoint.graph import GraphClient, GraphError
from tests.sharepoint_fake import DRIVE, TENANT, FakeMicrosoft, settings


@pytest.fixture
def fake() -> FakeMicrosoft:
    return FakeMicrosoft()


def graph(fake: FakeMicrosoft) -> GraphClient:
    return fake.microsoft().graph("access-1")


def test_the_graph_client_has_no_way_to_change_anything() -> None:
    source = inspect.getsource(graph_module)

    assert source.count("build_request(") == 1 and 'build_request("GET"' in source
    assert not re.search(r"\.(post|put|patch|delete|request|stream)\(", source)


def test_the_sign_in_asks_only_to_read() -> None:
    url = urlparse(MicrosoftAuth(settings()).authorize_url("state-1"))
    query = parse_qs(url.query)

    assert url.netloc == "login.microsoftonline.com"
    assert url.path == f"/{TENANT}/oauth2/v2.0/authorize"
    assert query["scope"] == ["offline_access User.Read Sites.Read.All Files.Read.All"]
    assert "Write" not in auth_module.SCOPES and "Manage" not in auth_module.SCOPES
    assert query["prompt"] == ["select_account"] and query["state"] == ["state-1"]
    assert query["redirect_uri"] == ["http://localhost:8000/sharepoint/callback"]


def test_not_configured_without_all_three_keys() -> None:
    assert MicrosoftAuth(settings()).configured()
    assert not MicrosoftAuth(settings(sharepoint_client_secret="")).configured()
    assert not MicrosoftAuth(settings(sharepoint_tenant_id="")).configured()


def test_a_refused_renewal_says_a_new_sign_in_is_needed(fake: FakeMicrosoft) -> None:
    fake.revoked = True

    with pytest.raises(SharePointAccessRevoked):
        fake.microsoft().auth(settings()).refresh("refresh-1")


def test_sites_come_page_by_page_without_the_users_own_onedrive(fake: FakeMicrosoft) -> None:
    names = [site.name for site in graph(fake).sites()]

    assert names == ["Finance", "ROAST HUB"]


def test_only_document_libraries_are_offered(fake: FakeMicrosoft) -> None:
    assert [lib.name for lib in graph(fake).libraries("s-roast")] == ["Documents"]


def test_a_folder_knows_its_path_inside_the_library(fake: FakeMicrosoft) -> None:
    inside = {item.name: item for item in graph(fake).children(DRIVE, "f-april")}

    assert inside["April 11"].path == "ROAST/OUTLETS/April/April 11"
    assert inside["April 11"].is_folder and inside["April 11"].child_count == 3
    assert graph(fake).item(DRIVE, "root").path == ""


def test_a_copied_link_and_a_folder_open_in_the_browser_both_lead_to_the_folder(
    fake: FakeMicrosoft,
) -> None:
    copied = "https://missan.sharepoint.com/:f:/s/ROASTHUB/April-made-up-token"
    browsing = (
        "https://missan.sharepoint.com/sites/ROASTHUB/Shared%20Documents/Forms/AllItems.aspx"
        "?id=%2Fsites%2FROASTHUB%2FShared%20Documents%2FROAST%2FOUTLETS%2FApril&viewid=1"
    )

    assert graph(fake).shared_item(copied).path == "ROAST/OUTLETS/April"
    assert graph(fake).shared_item(browsing).path == "ROAST/OUTLETS/April"


def test_an_address_that_is_not_sharepoint_is_refused_without_asking(fake: FakeMicrosoft) -> None:
    with pytest.raises(GraphError, match="SharePoint address"):
        graph(fake).shared_item("https://example.com/sites/ROASTHUB")

    assert fake.requests == []


def test_a_file_is_fetched_from_its_pre_signed_address_without_the_token(
    fake: FakeMicrosoft,
) -> None:
    out = io.BytesIO()

    graph(fake).download(DRIVE, "i-bill", out)

    assert out.getvalue() == b"%PDF-1.4 made-up bill"
    assert [r.url.host for r in fake.requests] == ["graph.microsoft.com", "missan.sharepoint.com"]


def test_the_token_never_follows_a_next_page_address_elsewhere() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [], "@odata.nextLink": "https://evil.test/x"})

    client = GraphClient("access-1", httpx.Client(transport=httpx.MockTransport(answer)))

    with pytest.raises(GraphError, match="another address"):
        client.sites()


def test_a_busy_sharepoint_is_asked_again_after_the_wait_it_names() -> None:
    answers = iter([httpx.Response(429, headers={"retry-after": "7"}),
                    httpx.Response(200, json={"value": []})])  # fmt: skip
    waits: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(lambda _: next(answers)))

    assert GraphClient("access-1", http, sleep=waits.append).sites() == []
    assert waits == [7]


def test_a_missing_folder_is_said_plainly(fake: FakeMicrosoft) -> None:
    with pytest.raises(GraphError, match="not found in SharePoint") as refused:
        graph(fake).children(DRIVE, "f-gone")

    assert refused.value.status == 404


def test_every_request_in_these_tests_only_reads(fake: FakeMicrosoft) -> None:
    client = graph(fake)
    client.me()
    client.sites()
    client.children(DRIVE, "f-a11")
    client.download(DRIVE, "i-photo", io.BytesIO())

    assert {r.method for r in fake.requests} == {"GET"}
