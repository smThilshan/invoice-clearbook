"""A made-up Microsoft for the SharePoint tests (decision 83): the sign-in, and one library on
one site holding a few folders and files, answered as Graph answers. No network, no real tokens.
Every request is kept, so a test can prove that nothing but reading was ever asked."""

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx

from app.adapters.sharepoint.auth import MicrosoftAuth
from app.adapters.sharepoint.graph import GraphClient
from app.core.config import Settings
from app.services.sharepoint import Microsoft

TENANT = "made-up-tenant"
DRIVE = "b!drive-1"
DOWNLOADS = "https://missan.sharepoint.com/download"


@dataclass
class Node:
    item_id: str
    name: str
    parent: str | None  # item id; None = the library itself
    content: bytes | None = None  # None = a folder


def library() -> dict[str, Node]:
    nodes = [
        Node("root", "root", None),
        Node("f-roast", "ROAST", "root"),
        Node("f-outlets", "OUTLETS", "f-roast"),
        Node("f-april", "April", "f-outlets"),
        Node("f-a11", "April 11", "f-april"),
        Node("i-bill", "bill.pdf", "f-a11", b"%PDF-1.4 made-up bill"),
        Node("i-photo", "photo.JPG", "f-a11", b"\xff\xd8\xff made-up photo"),
        Node("i-sheet", "totals.xlsx", "f-a11", b"not an invoice"),
        Node("f-a12", "April 12", "f-april"),
        Node("i-receipt", "receipt.pdf", "f-a12", b"%PDF-1.4 made-up receipt"),
        Node("i-lock", "~$receipt.pdf", "f-a12", b"office lock file"),
        Node("f-hidden", ".thumbs", "f-april"),
        Node("i-thumb", "thumb.jpg", "f-hidden", b"\xff\xd8\xff thumbnail"),
    ]
    return {node.item_id: node for node in nodes}


@dataclass
class FakeMicrosoft:
    nodes: dict[str, Node] = field(default_factory=library)
    requests: list[httpx.Request] = field(default_factory=list)
    issued: int = 0  # tokens handed out
    revoked: bool = False  # the refresh token is no longer accepted
    refused_tokens: set[str] = field(default_factory=set)  # access tokens Graph turns away

    def microsoft(self) -> Microsoft:
        http = httpx.Client(transport=httpx.MockTransport(self))
        return Microsoft(
            auth=lambda settings: MicrosoftAuth(settings, http),
            graph=lambda token: GraphClient(token, http, sleep=lambda _: None),
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "login.microsoftonline.com":
            return self._token(request)
        if url.host == "missan.sharepoint.com":
            assert "authorization" not in request.headers  # pre-signed: never our token
            return httpx.Response(200, content=self.nodes[url.path.rsplit("/", 1)[1]].content)
        assert url.host == "graph.microsoft.com"
        token = request.headers["authorization"].removeprefix("Bearer ")
        if token in self.refused_tokens:
            return httpx.Response(401, json={"error": {"message": "token expired"}})
        return self._graph(url)

    def graph_requests(self) -> Iterator[httpx.Request]:
        return (r for r in self.requests if r.url.host != "login.microsoftonline.com")

    def _token(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/{TENANT}/oauth2/v2.0/token"
        form = parse_qs(request.content.decode())
        if form["grant_type"] == ["refresh_token"] and self.revoked:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.issued += 1
        return httpx.Response(200, json={
            "access_token": f"access-{self.issued}", "refresh_token": f"refresh-{self.issued}",
            "expires_in": 3600,
        })  # fmt: skip

    def _graph(self, url: httpx.URL) -> httpx.Response:
        parts = url.path.removeprefix("/v1.0/").split("/")
        if parts == ["me"]:
            return httpx.Response(200, json={"displayName": "Made-up Admin",
                                             "mail": "admin@missan.test"})  # fmt: skip
        if parts == ["sites"]:
            return self._sites(url)
        if parts[0] == "sites" and parts[2:] == ["drives"]:
            return httpx.Response(200, json={"value": [
                {"id": DRIVE, "name": "Documents", "driveType": "documentLibrary"},
                {"id": "b!list", "name": "Site Pages", "driveType": "other"},
            ]})  # fmt: skip
        if parts[0] == "shares" and parts[2:] == ["driveItem"]:
            shared = base64.urlsafe_b64decode(parts[1].removeprefix("u!") + "==").decode()
            item = "f-april" if "April" in shared else "i-bill"
            return httpx.Response(200, json={"id": item, "parentReference": {"driveId": DRIVE}})
        if parts[:3] == ["drives", DRIVE, "items"] and parts[3] in self.nodes:
            node = self.nodes[parts[3]]
            rest = parts[4:]
            if rest == []:
                return httpx.Response(200, json=self._json(node))
            if rest == ["children"]:
                inside = [n for n in self.nodes.values() if n.parent == node.item_id]
                return httpx.Response(200, json={"value": [self._json(n) for n in inside]})
            if rest == ["content"]:
                return httpx.Response(302, headers={"location": f"{DOWNLOADS}/{node.item_id}"})
        return httpx.Response(404, json={"error": {"message": "itemNotFound"}})

    def _sites(self, url: httpx.URL) -> httpx.Response:
        if "page" not in url.params:  # the first page, then the next
            return httpx.Response(200, json={
                "value": [{"id": "s-roast", "displayName": "ROAST HUB",
                           "webUrl": "https://missan.sharepoint.com/sites/ROASTHUB"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites?search=*&page=2",
            })  # fmt: skip
        return httpx.Response(200, json={"value": [
            {"id": "s-finance", "displayName": "Finance",
             "webUrl": "https://missan.sharepoint.com/sites/Finance"},
            {"id": "s-onedrive", "displayName": "Made-up Admin",
             "webUrl": "https://missan-my.sharepoint.com/personal/admin"},
        ]})  # fmt: skip

    def _json(self, node: Node) -> dict[str, object]:
        body: dict[str, object] = {"id": node.item_id, "name": node.name}
        if node.parent is None:
            body["root"] = {}
            body["parentReference"] = {"driveId": DRIVE}
        else:
            body["parentReference"] = {"driveId": DRIVE, "path": self._path(node.parent)}
        if node.content is None:
            inside = sum(1 for n in self.nodes.values() if n.parent == node.item_id)
            body["folder"] = {"childCount": inside}
            body["size"] = 0
        else:
            body["file"] = {"mimeType": "application/octet-stream"}
            body["size"] = len(node.content)
        return body

    def _path(self, item_id: str) -> str:
        """Graph's parent path, percent-encoded: "/drives/<id>/root:/ROAST/April%2011"."""
        names: list[str] = []
        node = self.nodes[item_id]
        while node.parent is not None:
            names.insert(0, node.name.replace(" ", "%20"))
            node = self.nodes[node.parent]
        return f"/drives/{DRIVE}/root:" + "".join(f"/{name}" for name in names)


def settings(**changed: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "sharepoint_tenant_id": TENANT,
        "sharepoint_client_id": "made-up-app",
        "sharepoint_client_secret": "made-up-secret",
        "token_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
    }
    return Settings(_env_file=None, **(values | changed))  # type: ignore[arg-type]
