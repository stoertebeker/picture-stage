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
from collections.abc import Callable

from pebble import ProcessPool

from app.config import settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

_pool: ProcessPool | None = None


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
    timeout = settings.image_processing_timeout_seconds or None
    future = get_pool().schedule(func, args=list(args), timeout=timeout)
    result: T = await asyncio.wrap_future(future)
    return result
