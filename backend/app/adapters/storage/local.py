"""Storage on the local filesystem (MVP, decision 15)."""

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from app.adapters.storage.base import InvalidKeyError, NotFoundError, validate_key

_COPY_CHUNK = 1024 * 1024


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def put(self, key: str, data: bytes) -> None:
        self._write(key, lambda out: out.write(data))

    def put_file(self, key: str, source: Path) -> None:
        with source.open("rb") as src:
            self._write(key, lambda out: shutil.copyfileobj(src, out, _COPY_CHUNK))

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise NotFoundError(f"nothing stored under {key!r}") from None

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def _path(self, key: str) -> Path:
        return self._root.joinpath(*validate_key(key).split("/"))

    def _write(self, key: str, write: Callable[[BinaryIO], object]) -> None:
        """Write to a temp file in the target folder, then rename: readers and crashes never
        see a half-written file, so a re-run step can simply write again."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.resolve().is_relative_to(self._root):  # e.g. a symlinked folder
            raise InvalidKeyError(f"storage key escapes the store: {key!r}")

        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                write(out)
                out.flush()
                os.fsync(out.fileno())
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
