import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

import aiofiles

from app.security.signing import sign_url
from app.storage.base import StorageBackend


class LocalStorage(StorageBackend):
    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _full_path(self, key: str) -> Path:
        safe_key = Path(key)
        if safe_key.is_absolute() or ".." in safe_key.parts:
            raise ValueError("Invalid storage key")
        return self.base_path / safe_key

    async def upload(self, key: str, data: BinaryIO, content_type: str) -> str:
        path = self._full_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(path, "wb") as f:
            while chunk := data.read(65536):
                await f.write(chunk)

        return key

    async def download_stream(self, key: str, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        path = self._full_path(key)
        async with aiofiles.open(path, "rb") as f:
            while chunk := await f.read(chunk_size):
                yield chunk

    async def delete(self, key: str) -> None:
        path = self._full_path(key)
        if path.exists():
            os.remove(path)

    async def exists(self, key: str) -> bool:
        return self._full_path(key).exists()

    async def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        return sign_url(f"/media/{key}", expires_in=expires_in)

    async def copy(self, src_key: str, dst_key: str) -> None:
        src = self._full_path(src_key)
        dst = self._full_path(dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
