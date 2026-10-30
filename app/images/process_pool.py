"""Process pool for CPU-bound preview generation with a hard per-task timeout (q9td).

Pillow processing (resize/watermark/WebP-encode) runs in a pebble ``ProcessPool``
so a per-file timeout can *hard-kill* a hung worker process — unlike
``asyncio.to_thread``, whose thread keeps running after a ``wait_for`` timeout
(Python threads are not killable). The decompression-bomb pixel cap
(``max_image_pixels``) already prevents OOM; this bounds wall-clock time for a
degenerate-but-under-cap image.

The pool uses the ``spawn`` start method to stay safe inside the asyncio app
process: a forked child would inherit the running event loop and open DB
connections. With ``spawn`` the child imports ``app.images.processing`` fresh, so
the module-level ``Image.MAX_IMAGE_PIXELS`` cap is re-applied in every worker.

Created lazily on first use; closed in the FastAPI lifespan (``shutdown_pool``).
"""

import asyncio
import logging
import multiprocessing as mp
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar

from pebble import ProcessPool
from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

_pool: ProcessPool | None = None
_admission_lock = threading.Lock()
_active = 0
_waiting = 0
_admitted: ContextVar[asyncio.Task[object] | None] = ContextVar("image_processing_admitted", default=None)


class ImageProcessingBusy(Exception):
    """The image processing capacity or its bounded waiting room is full."""


class ImageAdmissionMiddleware:
    """Keep an upload's slot through its response and background tasks."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = get_route_path(scope) if scope["type"] == "http" else ""
        is_upload = (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and (
                re.fullmatch(r"/galleries/[^/]+/upload", path) or re.fullmatch(r"/api/v1/galleries/[^/]+/images", path)
            )
        )
        if not is_upload:
            await self.app(scope, receive, send)
            return
        entered = False
        try:
            async with image_processing_slot():
                entered = True
                await self.app(scope, receive, send)
        except ImageProcessingBusy:
            if entered:
                raise
            response = JSONResponse(
                {"detail": "Image processing is busy"}, status_code=503, headers={"Retry-After": "5"}
            )
            await response(scope, receive, send)


@asynccontextmanager
async def image_processing_slot() -> AsyncIterator[None]:
    """Reserve capacity before reading images or acquiring lifecycle fences.

    There are at most as many active jobs as workers and twice as many waiters.
    A waiter can wait at most five seconds. Limits apply per application process.
    """
    global _active, _waiting
    task = asyncio.current_task()
    if task is not None and _admitted.get() is task:
        yield
        return
    limit = max(1, settings.image_processing_workers)
    with _admission_lock:
        if _active < limit:
            _active += 1
            waiting = False
        elif _waiting < limit * 2:
            _waiting += 1
            waiting = True
        else:
            raise ImageProcessingBusy
    if waiting:
        deadline = time.monotonic() + 5
        try:
            while True:
                with _admission_lock:
                    if _active < limit:
                        _active += 1
                        _waiting -= 1
                        waiting = False
                        break
                if time.monotonic() >= deadline:
                    raise ImageProcessingBusy
                await asyncio.sleep(0.05)
        finally:
            if waiting:
                with _admission_lock:
                    _waiting -= 1
    token = _admitted.set(task)
    try:
        yield
    finally:
        _admitted.reset(token)
        with _admission_lock:
            _active -= 1


def get_pool() -> ProcessPool:
    """Return the shared preview ProcessPool, creating it on first use."""
    global _pool
    if _pool is None:
        workers = max(1, settings.image_processing_workers)
        # spawned workers don't inherit the parent's logging config — re-apply it
        # per worker so any worker-side logs use the same format (picture-stage-vblf).
        _pool = ProcessPool(
            max_workers=workers,
            context=mp.get_context("spawn"),
            initializer=configure_logging,
        )
        logger.info("Preview ProcessPool started (spawn, %d workers)", workers)
    return _pool


def shutdown_pool() -> None:
    """Close the pool and join its workers; safe to call when no pool exists."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool.join()
        _pool = None
        logger.info("Preview ProcessPool shut down")


async def run_in_pool[T](func: Callable[..., T], *args: object) -> T:
    """Run ``func`` in the process pool with the configured hard timeout.

    On timeout pebble kills the worker process and the awaited future raises
    ``TimeoutError`` (from ``concurrent.futures``); the worker is restarted
    automatically for the next task. ``image_processing_timeout_seconds`` of 0
    disables the timeout.
    """
    async with image_processing_slot():
        timeout = settings.image_processing_timeout_seconds or None
        future = asyncio.wrap_future(get_pool().schedule(func, args=list(args), timeout=timeout))
        try:
            result: T = await asyncio.shield(future)
        except asyncio.CancelledError:
            # Pebble reports cancellation before it has stopped a process. Keep
            # the slot until the underlying work really finishes or times out.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if future.done() and not future.cancelled():
                future.exception()
            raise
        return result
