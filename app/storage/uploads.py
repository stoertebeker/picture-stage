"""Wait for an in-flight storage write before allowing caller cancellation.

Cancelling an aiofiles await does not stop an already running OS thread. A
caller must retain its lifecycle fence until that write finishes, otherwise
cleanup can unlink nothing and the thread can create an orphan afterwards.
"""

import asyncio
from typing import BinaryIO

from anyio import CancelScope

from app.storage.base import StorageBackend


async def complete_upload(storage: StorageBackend, key: str, data: BinaryIO, content_type: str) -> str:
    return await _complete_write(asyncio.create_task(storage.upload(key, data, content_type)))


async def complete_copy(storage: StorageBackend, source: str, destination: str) -> None:
    await _complete_write(asyncio.create_task(storage.copy(source, destination)))


async def _complete_write[T](write: asyncio.Task[T]) -> T:
    try:
        return await asyncio.shield(write)
    except asyncio.CancelledError:
        # AnyIO level cancellation and repeated direct Task.cancel() must not
        # let cleanup overtake the write. Keep only I/O in the child task; DB
        # ownership and the caller's gallery/image fences stay in its own task.
        with CancelScope(shield=True):
            while not write.done():
                try:
                    await asyncio.shield(write)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not write.cancelled():
                write.exception()  # Retrieve failure; the original cancellation wins.
        raise
