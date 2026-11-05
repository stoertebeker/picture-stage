"""Finite restore budgets, including work hidden inside gzip and tar parsers.

The defaults accommodate large operator backups, but are never unlimited. Peak
temporary disk use is the compressed snapshot plus expanded archive plus a second
copy of local images on the upload filesystem. The deadline is cooperative:
regular-file kernel I/O and database subprocesses cannot be interrupted here.
"""

from __future__ import annotations

import gzip
import json
import math
import tarfile
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import IO, Any, BinaryIO, cast

CHUNK = 64 * 1024
_EXTENSIONS = (
    tarfile.XHDTYPE,
    tarfile.XGLTYPE,
    tarfile.SOLARIS_XHDTYPE,
    tarfile.GNUTYPE_LONGNAME,
    tarfile.GNUTYPE_LONGLINK,
)


@dataclass(frozen=True)
class RestoreLimits:
    input_bytes: int = 100 * 1024**3
    expanded_bytes: int = 512 * 1024**3
    file_bytes: int = 64 * 1024**3
    headers: int = 500_000
    objects: int = 500_000
    path_depth: int = 16
    path_bytes: int = 4096
    extension_bytes: int = 64 * 1024
    extensions_bytes: int = 16 * 1024**2
    extension_depth: int = 16
    ratio: int = 1000
    seconds: float = 900
    json_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or (isinstance(value, float) and not math.isfinite(value))
                or value <= 0
            ):
                raise ValueError(f"Restore limit {field.name} must be positive and finite")
            if field.name != "seconds" and not isinstance(value, int):
                raise ValueError(f"Restore limit {field.name} must be an integer")


class RestoreBudget:
    def __init__(self, limits: RestoreLimits) -> None:
        self.limits = limits
        self.deadline = time.monotonic() + limits.seconds
        self.input_bytes = 0
        self.compressed_bytes = 0
        self.expanded_bytes = 0
        self.headers = 0
        self.extension_bytes = 0
        self.extension_depth = 0

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise ValueError("Restore archive processing deadline exceeded")

    def input(self, size: int) -> None:
        self.check()
        if size > self.limits.input_bytes - self.input_bytes:
            raise ValueError("Restore compressed input byte limit exceeded")
        self.input_bytes += size

    def expanded(self, size: int) -> None:
        self.check()
        if size > self.limits.expanded_bytes - self.expanded_bytes:
            raise ValueError("Restore expanded byte limit exceeded")
        self.expanded_bytes += size
        # Count only compressed bytes actually read, never stat size: an
        # appended incompressible tail must not dilute a preceding bomb.
        if self.expanded_bytes > max(1, self.compressed_bytes) * self.limits.ratio:
            raise ValueError("Restore compression ratio limit exceeded")

    def file(self, size: int) -> None:
        self.check()
        if size < 0 or size > self.limits.file_bytes:
            raise ValueError("Restore single-file byte limit exceeded")


class _CompressedReader:
    def __init__(self, source: BinaryIO, budget: RestoreBudget) -> None:
        self.source = source
        self.budget = budget

    def read(self, size: int = -1) -> bytes:
        self.budget.check()
        # Bound read-ahead included in the compression-ratio denominator to
        # 8 KiB, including newer gzip runtimes requesting larger input chunks.
        data = self.source.read(min(size, 8192) if size >= 0 else 8192)
        self.budget.check()
        self.budget.compressed_bytes += len(data)
        return data


class _ExpandedReader:
    def __init__(self, source: gzip.GzipFile, budget: RestoreBudget) -> None:
        self.source = source
        self.budget = budget

    def read(self, size: int = -1) -> bytes:
        self.budget.check()
        # GzipFile.read(n) bounds zlib output; tarfile's r|gz decompressor does
        # not. Never pass an unbounded read through either parser boundary.
        size = min(size, CHUNK) if size >= 0 else CHUNK
        data = self.source.read(min(size, self.budget.limits.expanded_bytes - self.budget.expanded_bytes + 1))
        self.budget.expanded(len(data))
        return data


class _ReplayReader:
    """Replay one prevalidated PAX body to the stdlib parser without seeking."""

    def __init__(self, source: Any, body: bytes) -> None:
        self.source = source
        self.body = body

    def read(self, size: int = -1) -> bytes:
        if self.body:
            if size != len(self.body):
                raise ValueError("Unsupported tar extension parser read")
            body, self.body = self.body, b""
            return body
        return self.source.read(size)  # type: ignore[no-any-return]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.source, name)


def _validate_pax(body: bytes, size: int, budget: RestoreBudget) -> None:
    """Reject sparse formats before stdlib's potentially allocating handlers."""
    offset = 0
    body = body[:size]
    while offset < len(body):
        budget.check()
        space = body.find(b" ", offset)
        number = body[offset:space] if space >= 0 else b""
        if not number.isdigit() or len(number) > 10:
            raise ValueError("Invalid PAX record length")
        length = int(number)
        if length < 5 or offset + length > len(body):
            raise ValueError("Invalid PAX record length")
        record = body[space + 1 : offset + length]
        key, equal, value = record[:-1].partition(b"=")
        if not equal or not key or not record.endswith(b"\n"):
            raise ValueError("Invalid PAX record")
        if key.startswith(b"GNU.sparse") or key in (b"SCHILY.realsize", b"SCHILY.filetype"):
            raise ValueError("Sparse archive members are not supported")
        if key == b"size":
            if not value.isdigit() or len(value) > 20:
                raise ValueError("Invalid PAX file size")
            budget.file(int(value))
        offset += length


def _guarded_tarinfo(budget: RestoreBudget) -> type[tarfile.TarInfo]:
    # These two private hooks exist in supported CPython 3.12 and newer. They
    # run before stdlib eagerly reads extension bodies or follows recursive
    # extension headers; checking only yielded TarInfos would be too late.
    class GuardedTarInfo(tarfile.TarInfo):
        @classmethod
        def fromtarfile(cls, archive: Any) -> Any:
            try:
                return super().fromtarfile(archive)
            except tarfile.EOFHeaderError:  # type: ignore[attr-defined]
                raise
            except tarfile.HeaderError as exc:
                # TarFile.next() otherwise treats malformed later headers as
                # EOF, silently discarding the offending block.
                raise ValueError(f"Invalid tar header: {exc}") from None

        def _proc_member(self, archive: Any) -> Any:
            budget.check()
            budget.headers += 1
            if budget.headers > budget.limits.headers:
                raise ValueError("Restore tar header count limit exceeded")
            budget.file(self.size)
            if self.type == tarfile.GNUTYPE_SPARSE:
                raise ValueError("Sparse archive members are not supported")
            if self.type not in _EXTENSIONS:
                return super()._proc_member(archive)  # type: ignore[misc]
            if self.size > budget.limits.extension_bytes:
                raise ValueError("Restore tar extension byte limit exceeded")
            budget.extension_bytes += self.size
            if budget.extension_bytes > budget.limits.extensions_bytes:
                raise ValueError("Restore cumulative tar extension byte limit exceeded")
            budget.extension_depth += 1
            try:
                if budget.extension_depth > budget.limits.extension_depth:
                    raise ValueError("Restore tar extension chain limit exceeded")
                return super()._proc_member(archive)  # type: ignore[misc]
            finally:
                budget.extension_depth -= 1

        def _proc_pax(self, archive: Any) -> Any:
            source = archive.fileobj
            body = source.read((self.size + 511) // 512 * 512)
            if len(body) != (self.size + 511) // 512 * 512:
                raise ValueError("Truncated PAX extension")
            _validate_pax(body, self.size, budget)
            archive.fileobj = _ReplayReader(source, body)
            try:
                return super()._proc_pax(archive)  # type: ignore[misc]
            finally:
                archive.fileobj = source

    return GuardedTarInfo


def _target(name: str, budget: RestoreBudget) -> tuple[str, ...]:
    budget.check()
    try:
        encoded = name.encode("utf-8")
    except UnicodeError:
        raise ValueError("Archive path is not valid UTF-8") from None
    parts = tuple(name.rstrip("/").split("/"))
    if len(encoded) > budget.limits.path_bytes or len(parts) > budget.limits.path_depth:
        raise ValueError("Restore path byte/depth limit exceeded")
    if any(part in ("", ".", "..") for part in parts) or "\x00" in name or "\\" in name:
        raise ValueError("Invalid archive path or path traversal")
    if parts[0] != "images" and parts not in (("metadata.json",), ("manifest.json",), ("db.dump",), ("db.sql",)):
        raise ValueError("Unsupported archive root")
    return parts


def extract_archive(snapshot: BinaryIO, destination: Path, budget: RestoreBudget) -> None:
    """Extract only regular backup files under finite CPU/memory/disk budgets."""
    seen: set[tuple[str, ...]] = set()
    objects: dict[tuple[str, ...], bool] = {}
    compressed = _CompressedReader(snapshot, budget)
    with gzip.GzipFile(fileobj=cast(Any, compressed), mode="rb") as gz:
        expanded = _ExpandedReader(gz, budget)
        with tarfile.open(fileobj=cast(Any, expanded), mode="r|", tarinfo=_guarded_tarinfo(budget)) as archive:
            while (member := archive.next()) is not None:
                archive.members.clear()  # type: ignore[attr-defined]  # CPython 3.12 retains these in r|.
                budget.file(member.size)
                if not (member.isdir() or member.isreg()) or member.sparse is not None:
                    raise ValueError("Archive links, devices, FIFOs and sparse files are not supported")
                parts = _target(member.name, budget)
                if parts in seen:
                    raise ValueError("Duplicate canonical archive target")
                seen.add(parts)
                if member.isdir() and (parts[0] != "images" or member.size):
                    raise ValueError("Invalid archive directory")
                if member.isreg() and parts == ("images",):
                    raise ValueError("images must be a directory")
                if parts[0] in ("metadata.json", "manifest.json") and member.size > budget.limits.json_bytes:
                    raise ValueError("Restore JSON byte limit exceeded")
                for index in range(1, len(parts) + 1):
                    path = parts[:index]
                    directory = index < len(parts) or member.isdir()
                    if path in objects and objects[path] != directory:
                        raise ValueError("Conflicting archive targets")
                    objects[path] = directory
                if len(objects) > budget.limits.objects:
                    raise ValueError("Restore filesystem object count limit exceeded")
                target = destination.joinpath(*parts)
                budget.check()
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Missing archive file data")
                with source, target.open("xb") as output:
                    copy_bounded(source, output, budget, expected=member.size)
            # Drain the tar stream, including its read-ahead buffer, rather
            # than the underlying gzip reader: concatenated nonzero payloads
            # after the first tar end marker must never be silently ignored.
            while chunk := archive.fileobj.read(CHUNK):
                budget.check()
                if chunk.strip(b"\0"):
                    raise ValueError("Nonzero data after tar end marker")
        # Force gzip CRC/trailer validation, including concatenated gzip
        # streams. All trailing expansion (even zero padding) stays budgeted.
        while chunk := expanded.read(CHUNK):
            if chunk.strip(b"\0"):
                raise ValueError("Nonzero data after tar end marker")


def copy_bounded(source: IO[bytes], output: IO[bytes], budget: RestoreBudget, *, expected: int) -> None:
    budget.file(expected)
    copied = 0
    while True:
        budget.check()
        chunk = source.read(min(CHUNK, expected - copied + 1))
        budget.check()
        if not chunk:
            break
        copied += len(chunk)
        if copied > expected:
            raise ValueError("File grew beyond its validated restore size")
        output.write(chunk)
    if copied != expected:
        raise ValueError("Truncated restore file")


def read_json(path: Path, budget: RestoreBudget, *, manifest: bool = False) -> dict[str, Any]:
    budget.check()
    with path.open("rb") as source:
        data = source.read(budget.limits.json_bytes + 1)
    budget.check()
    if len(data) > budget.limits.json_bytes:
        raise ValueError("Restore JSON byte limit exceeded")
    try:
        value = json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"Invalid {path.name}: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    string_fields = (
        ("bucket", "endpoint", "region", "note") if manifest else ("app_version", "timestamp", "storage_type")
    )
    for field in string_fields:
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"Invalid {path.name} field {field}")
    if not manifest:
        if value.get("storage_type", "local") not in ("local", "s3"):
            raise ValueError("Invalid backup storage_type")
        for field in ("backup_format", "image_count"):
            if field in value and (type(value[field]) is not int or value[field] < 0):
                raise ValueError(f"Invalid metadata field {field}")
    return value
