from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import BinaryIO


class StorageBackend(ABC):
    @abstractmethod
    async def upload(self, key: str, data: BinaryIO, content_type: str) -> str:
        """Upload file, return the canonical storage key."""

    @abstractmethod
    async def download_stream(self, key: str, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        """Stream file content in chunks."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete a file by key."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if a file exists."""

    @abstractmethod
    async def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a time-limited URL for the file."""


def storage_key(gallery_id: str, category: str, filename: str) -> str:
    return f"{gallery_id}/{category}/{filename}"
