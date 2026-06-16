"""Upload input limits shared by the API and frontend upload paths (picture-stage-fbq).

Both upload handlers read each file fully into memory; without a cap a single huge
file (OOM) or a request with thousands of files can exhaust resources. These guards
bound per-file size and per-request file count. Limits are read from ``settings`` on
every call, so changing them in ``.env`` + restarting applies to all uploads
(including existing galleries). Both upload endpoints are authenticated (active
photographers only), so this is hardening, not an anonymous DoS surface.

A value of 0 disables the respective limit.
"""

from fastapi import HTTPException, UploadFile, status

from app.config import settings


def enforce_file_count(count: int) -> None:
    """Reject a request that carries more files than ``max_files_per_upload``."""
    max_files = settings.max_files_per_upload
    if max_files and count > max_files:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Too many files in one upload (max {max_files})",
        )


async def read_within_limit(file: UploadFile) -> bytes:
    """Read an upload fully, but abort with 413 if it exceeds the per-file cap.

    Reads at most ``max + 1`` bytes (not the whole stream) so an oversized file
    can't OOM the worker before the check fires.
    """
    max_mb = settings.max_upload_file_mb
    if not max_mb:
        return await file.read()

    max_bytes = max_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File too large (max {max_mb} MB)",
        )
    return data
