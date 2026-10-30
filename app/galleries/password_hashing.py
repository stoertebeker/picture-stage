"""Bound the CPU cost of gallery-password hashing in async request handlers."""

import asyncio
import threading

from fastapi import HTTPException, status

from app.auth.passwords import hash_password

_lock = threading.Lock()
_running = 0
_MAX_CONCURRENT_HASHES = 2


class GalleryPasswordBusy(HTTPException):
    """Gallery-password hashing has no free capacity."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gallery password processing is busy",
            headers={"Retry-After": "1"},
        )


def _hash_and_release(password: str) -> str:
    global _running
    try:
        return hash_password(password)
    finally:
        with _lock:
            _running -= 1


async def hash_gallery_password(password: str) -> str:
    """Hash off the event loop, admitting at most two hashes per app process.

    The worker releases capacity when bcrypt actually finishes, including when
    the request is cancelled. Saturated requests do not enter the thread queue.
    """
    global _running
    with _lock:
        if _running >= _MAX_CONCURRENT_HASHES:
            raise GalleryPasswordBusy
        _running += 1

    try:
        future = asyncio.get_running_loop().run_in_executor(None, _hash_and_release, password)
    except BaseException:
        with _lock:
            _running -= 1
        raise

    # A disconnected caller must not cancel the executor future or free a slot
    # before its non-cancellable thread has finished. Consume abandoned errors.
    def consume_result(done: asyncio.Future[str]) -> None:
        if not done.cancelled():
            done.exception()

    future.add_done_callback(consume_result)
    return await asyncio.shield(future)
