"""Storage interface for files (local filesystem now, MinIO later)."""

from app.adapters.storage.base import InvalidKeyError, NotFoundError, Storage, StorageError
from app.adapters.storage.local import LocalStorage
from app.core.config import Settings, get_settings

__all__ = ["InvalidKeyError", "NotFoundError", "Storage", "StorageError", "get_storage"]


def get_storage(settings: Settings | None = None) -> Storage:
    settings = settings or get_settings()
    return LocalStorage(settings.store_dir)
