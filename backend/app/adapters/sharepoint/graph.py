"""Reading SharePoint through Microsoft Graph (decision 83). Read only, by construction.

Every call goes through `_get`, the one place that sends a request, and it only ever sends GET:
there is no code here that could create, change, move or delete anything (tests hold this). The
access token goes only to Graph itself; a file's download address (pre-signed by Microsoft) is
fetched without it. Errors never carry the token or file contents.

Items are addressed by their ids (drive + item), which stay the same when a folder is renamed;
`path` (inside the library) is kept for the screen and for reading a date from folder names.
"""

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, BinaryIO
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

GRAPH_URL = "https://graph.microsoft.com/v1.0"
TIMEOUT_SECONDS = 60.0
ATTEMPTS = 4  # throttled or briefly unavailable: wait and try again, then give up
MAX_WAIT_SECONDS = 30
MAX_PAGES = 100  # 20,000 entries in one listing: far above any folder of invoices
PAGE_SIZE = 200
_ITEM_FIELDS = "id,name,folder,file,size,parentReference,webUrl,root"


class GraphError(Exception):
    """Graph refused a request or could not be reached. `status`: the HTTP status, if any."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def access(self) -> bool:
        """The access token itself was refused (expired or withdrawn): renew it."""
        return self.status == httpx.codes.UNAUTHORIZED


@dataclass(frozen=True, slots=True)
class Account:
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class Site:
    site_id: str
    name: str
    web_url: str


@dataclass(frozen=True, slots=True)
class Library:
    drive_id: str
    name: str


@dataclass(frozen=True, slots=True)
class Item:
    drive_id: str
    item_id: str
    name: str
    path: str  # inside its library, e.g. "ROAST/OUTLETS/April"; "" = the library itself
    is_folder: bool
    size: int
    child_count: int | None  # folders: entries directly inside


class GraphClient:
    def __init__(
        self,
        access_token: str,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._sleep = sleep

    def me(self) -> Account:
        body = self._get(f"{GRAPH_URL}/me", {"$select": "displayName,mail,userPrincipalName"})
        return Account(
            body.get("displayName") or "", body.get("mail") or body.get("userPrincipalName") or ""
        )

    def sites(self) -> list[Site]:
        """The SharePoint sites the signed-in user can open (their OneDrive left out)."""
        found = [
            Site(
                row["id"], row.get("displayName") or row.get("name") or "", row.get("webUrl") or ""
            )
            for row in self._pages(
                f"{GRAPH_URL}/sites", {"search": "*", "$select": "id,displayName,name,webUrl"}
            )
        ]
        return sorted(
            (s for s in found if "-my.sharepoint.com" not in s.web_url),
            key=lambda s: s.name.lower(),
        )

    def libraries(self, site_id: str) -> list[Library]:
        rows = self._pages(
            f"{GRAPH_URL}/sites/{quote(site_id, safe='')}/drives", {"$select": "id,name,driveType"}
        )
        return [
            Library(r["id"], r.get("name") or "")
            for r in rows
            if r.get("driveType") == "documentLibrary"
        ]

    def item(self, drive_id: str, item_id: str) -> Item:
        return _item(self._get(f"{_item_url(drive_id, item_id)}", {"$select": _ITEM_FIELDS}))

    def children(self, drive_id: str, item_id: str) -> list[Item]:
        rows = self._pages(
            f"{_item_url(drive_id, item_id)}/children",
            {"$select": _ITEM_FIELDS, "$top": str(PAGE_SIZE)},
        )
        return [_item(row) for row in rows]

    def shared_item(self, url: str) -> Item:
        """The item a pasted SharePoint address points to: a "Copy link" link, or the address of
        a folder open in the browser."""
        shared = self._get(
            f"{GRAPH_URL}/shares/{_share_id(url)}/driveItem", {"$select": "id,parentReference"}
        )
        drive_id = (shared.get("parentReference") or {}).get("driveId")
        if not drive_id or not shared.get("id"):
            raise GraphError("this link does not lead to a SharePoint file or folder")
        return self.item(drive_id, shared["id"])  # with its full path, which /shares leaves out

    def download(self, drive_id: str, item_id: str, out: BinaryIO) -> None:
        """Write the file's content to `out`. Graph answers with a pre-signed address on
        SharePoint; that one is fetched without our token."""
        response = self._send(
            f"{_item_url(drive_id, item_id)}/content", {}, authorised=True, stream=False
        )
        location = response.headers.get("location")
        if response.status_code == httpx.codes.OK:
            out.write(response.content)
            return
        if not location or urlparse(location).scheme != "https":
            raise GraphError("SharePoint did not give the file's download address")
        streamed = self._send(location, {}, authorised=False, stream=True)
        try:
            for chunk in streamed.iter_bytes():
                out.write(chunk)
        except httpx.HTTPError as exc:
            raise GraphError(f"the download broke off ({type(exc).__name__})") from exc
        finally:
            streamed.close()

    def _pages(self, url: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            body = self._get(url, params)
            rows += body.get("value") or []
            url, params = body.get("@odata.nextLink") or "", {}
            if not url:
                break
        return rows

    def _get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        response = self._send(url, params, authorised=True, stream=False)
        try:
            body = response.json()
        except ValueError as exc:
            raise GraphError("SharePoint answered something that is not JSON") from exc
        return body if isinstance(body, dict) else {}

    def _send(
        self, url: str, params: dict[str, str], *, authorised: bool, stream: bool
    ) -> httpx.Response:
        """The one place a request leaves: always GET. The token only ever goes to Graph."""
        if authorised and not url.startswith(f"{GRAPH_URL}/"):
            raise GraphError("refused to send the SharePoint access to another address")
        headers = self._headers if authorised else {}
        for attempt in range(1, ATTEMPTS + 1):
            # No params: keep the address's own query (a next page's), which httpx replaces
            request = self._http.build_request("GET", url, params=params or None, headers=headers)
            try:
                response = self._http.send(request, stream=stream)
            except httpx.HTTPError as exc:
                if attempt == ATTEMPTS:
                    raise GraphError(f"cannot reach SharePoint ({type(exc).__name__})") from exc
                self._sleep(2**attempt)
                continue
            if not _busy(response) or attempt == ATTEMPTS:
                break
            response.close()
            self._sleep(_wait(response, attempt))
        if response.is_error:
            response.read()
            raise _error(response)
        return response


def _item_url(drive_id: str, item_id: str) -> str:
    return f"{GRAPH_URL}/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}"


def _item(row: dict[str, Any]) -> Item:
    parent = row.get("parentReference") or {}
    folder = row.get("folder")
    inside = ""
    if "root" not in row:
        # "/drives/<id>/root:/ROAST/OUTLETS" -> "ROAST/OUTLETS"
        above = unquote((parent.get("path") or "").partition("root:")[2]).strip("/")
        inside = f"{above}/{row.get('name', '')}" if above else row.get("name", "")
    return Item(
        drive_id=parent.get("driveId") or "",
        item_id=row["id"],
        name=row.get("name") or "",
        path=inside,
        is_folder=folder is not None,
        size=int(row.get("size") or 0),
        child_count=folder.get("childCount") if isinstance(folder, dict) else None,
    )


def _share_id(url: str) -> str:
    """Graph's id for a shared address ("u!" + unpadded base64url). A folder open in the browser
    (".../AllItems.aspx?id=/sites/X/Shared Documents/A") stands for that folder's own address."""
    address = url.strip()
    parsed = urlparse(address)
    if parsed.scheme != "https" or not parsed.netloc.endswith(".sharepoint.com"):
        raise GraphError("paste a SharePoint address (https://….sharepoint.com/…)")
    folder = parse_qs(parsed.query).get("id")
    if folder and folder[0].startswith("/"):
        address = f"https://{parsed.netloc}{quote(folder[0])}"
    return "u!" + base64.urlsafe_b64encode(address.encode()).decode().rstrip("=")


def _busy(response: httpx.Response) -> bool:
    return response.status_code == httpx.codes.TOO_MANY_REQUESTS or response.status_code in (
        502,
        503,
        504,
    )


def _wait(response: httpx.Response, attempt: int) -> float:
    asked = response.headers.get("retry-after", "")
    return min(int(asked), MAX_WAIT_SECONDS) if asked.isdigit() else float(2**attempt)


def _error(response: httpx.Response) -> GraphError:
    status = response.status_code
    try:
        message = str(((response.json() or {}).get("error") or {}).get("message") or "")[:200]
    except (ValueError, AttributeError):
        message = ""
    plain = {
        401: "SharePoint refused the sign-in",
        403: "you do not have access to this in SharePoint",
        404: "not found in SharePoint (moved, renamed or deleted?)",
    }.get(status, f"SharePoint answered HTTP {status}")
    return GraphError(
        f"{plain}{f': {message}' if message and status not in (403, 404) else ''}", status=status
    )
