"""Storage port: every file the system keeps goes through here (CLAUDE.md rule 5).

Keys are relative '/'-separated paths built by `keys.py`. The same keys work for every backend
(local filesystem now, MinIO later), so switching is configuration, not code.
"""

import re
from pathlib import Path
from typing import Protocol

# Safe for a filesystem and for S3/MinIO object names alike.
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class StorageError(Exception):
    pass


class InvalidKeyError(StorageError, ValueError):
    pass


class NotFoundError(StorageError):
    pass


def validate_key(key: str) -> str:
    """Reject anything that is not a plain relative path (no '..', no absolute or empty parts)."""
    segments = key.split("/")
    if not key or not all(_SEGMENT.fullmatch(s) and s not in (".", "..") for s in segments):
        raise InvalidKeyError(f"invalid storage key: {key!r}")
    return key


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None:
        """Store `data` under `key`, replacing any existing object. Atomic."""
        ...

    def put_file(self, key: str, source: Path) -> None:
        """Stream a file into storage without loading it into memory. Atomic."""
        ...

    def get(self, key: str) -> bytes:
        """Raises NotFoundError if nothing is stored under `key`."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None:
        """Remove what is stored under `key`; nothing stored there is not an error."""
        ...
