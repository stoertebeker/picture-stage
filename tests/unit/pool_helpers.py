"""Top-level helpers for process-pool tests (picture-stage-q9td).

These must live in an importable module (not inside a test function) so that
``spawn`` worker processes can re-import them by qualified name.
"""

import time


def add(a: int, b: int) -> int:
    return a + b


def sleep_seconds(seconds: float) -> str:
    time.sleep(seconds)
    return "done"


def get_max_image_pixels() -> int | None:
    """Return the Pillow pixel cap as seen *inside the worker process*.

    Importing ``app.images.processing`` is what re-applies the module-level cap in
    a spawn worker — exactly what happens when a worker runs ``render_*`` (those
    functions live in that module), so this mirrors the real processing path.
    """
    from PIL import Image

    import app.images.processing  # noqa: F401  (import side effect: applies the cap)

    return Image.MAX_IMAGE_PIXELS
