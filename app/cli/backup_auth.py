"""Authenticate complete backup bytes before any archive parsing or restore work."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from app.cli.restore_archive import CHUNK, RestoreBudget, RestoreLimits

_DOMAIN = b"picture-stage/backup-authentication/v1"
_SIGNATURE_PREFIX = b"picture-stage-backup-hmac-sha256-v1:"


def signing_key(secret: str) -> bytes:
    """Derive a backup-only key from the existing operator-held HMAC secret."""
    raw = secret.encode("utf-8")
    if len(raw.strip()) < 32 or secret.strip().upper() == "CHANGE_ME":
        raise ValueError(
            "Backup authentication requires HMAC_SECRET_KEY with at least 32 bytes of random secret material"
        )
    return hmac.digest(raw, _DOMAIN, "sha256")


def signature_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".sig")


def _signature(digest: hmac.HMAC) -> bytes:
    return _SIGNATURE_PREFIX + digest.hexdigest().encode("ascii") + b"\n"


def sign_archive(archive: Path, key: bytes) -> bytes:
    digest = hmac.new(key, digestmod=hashlib.sha256)
    with archive.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return _signature(digest)


@contextmanager
def authenticated_snapshot(
    archive: Path, secret: str, *, trust_legacy: bool, budget: RestoreBudget | None = None
) -> Iterator[tuple[BinaryIO, bool]]:
    """Verify and extract from one private snapshot, never reopen the input path."""
    budget = budget or RestoreBudget(RestoreLimits())
    budget.check()
    sidecar = signature_path(archive)
    signature: bytes | None = None
    try:
        entry = sidecar.lstat()
    except FileNotFoundError:
        if not trust_legacy:
            raise ValueError(
                "Archive signature is missing. Only old backups you own and trust may use --trust-legacy-unsigned"
            ) from None
    else:
        if not stat.S_ISREG(entry.st_mode):
            raise ValueError("Archive signature must be a regular file")
        # A replacement symlink must not turn an unreadable signature into an
        # unsigned legacy input. All read and verification failures fail closed.
        descriptor = os.open(sidecar, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("Archive signature must be a regular file")
            signature = source.read(128)

    # A genuinely unsigned legacy backup uses the operator's explicit trust,
    # and must remain recoverable without an unrelated, unused signing key.
    digest = hmac.new(signing_key(secret), digestmod=hashlib.sha256) if signature is not None else None
    # This separate, unnamed file cannot be overwritten by extracted members.
    # TemporaryFile is private to the process and the source is opened once.
    with tempfile.TemporaryFile("w+b") as snapshot:
        descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            entry = os.fstat(source.fileno())
            if not stat.S_ISREG(entry.st_mode):
                raise ValueError("Archive input must be a regular file")
            if entry.st_size > budget.limits.input_bytes:
                raise ValueError("Restore compressed input byte limit exceeded")
            while True:
                budget.check()
                chunk = source.read(min(CHUNK, budget.limits.input_bytes - budget.input_bytes + 1))
                budget.input(len(chunk))
                if not chunk:
                    break
                snapshot.write(chunk)
                if digest is not None:
                    digest.update(chunk)
        if signature is not None and digest is not None and not hmac.compare_digest(signature, _signature(digest)):
            raise ValueError(
                "Archive signature is invalid; archive is modified, incomplete, or signed with another key"
            )
        budget.check()
        snapshot.seek(0)
        yield snapshot, signature is not None


def publish_backup(archive: Path, output: Path, signature: bytes) -> None:
    """Publish a completed pair without overwriting any existing backup files.

    The archive is the completion marker. A reader racing publication either
    sees no archive or a complete signed pair; any mismatch fails verification.
    """
    sidecar = signature_path(output)
    staged_signature = archive.with_name("signature")
    with staged_signature.open("xb") as destination:
        destination.write(signature)
        destination.flush()
        os.fsync(destination.fileno())
    archive.chmod(0o600)
    staged_signature.chmod(0o600)
    with archive.open("rb") as source:
        os.fsync(source.fileno())
    os.link(staged_signature, sidecar)
    try:
        os.link(archive, output)
    except OSError:
        sidecar.unlink()
        raise
