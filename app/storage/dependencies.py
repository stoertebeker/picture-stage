from functools import lru_cache

from app.config import settings
from app.storage.base import StorageBackend
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


@lru_cache(maxsize=1)
def _create_storage() -> StorageBackend:
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(base_path=settings.upload_dir)


def get_storage() -> StorageBackend:
    return _create_storage()
