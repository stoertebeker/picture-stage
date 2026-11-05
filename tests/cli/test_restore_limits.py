"""Small adversarial archives exercise finite limits without bombs or services."""

import argparse
import gzip
import io
import json
import os
import stat
import subprocess
import tarfile
from dataclasses import fields
from pathlib import Path

import pytest

from app.cli import backup
from app.cli.backup_auth import authenticated_snapshot, sign_archive, signature_path, signing_key
from app.cli.restore_archive import CHUNK, RestoreBudget, RestoreLimits, extract_archive
from app.config import settings

SECRET = "restore-budget-tests-only-secret-at-least-32-bytes"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "hmac_secret_key", SECRET)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))


def _archive(tmp_path, *, legacy=False, members=(), metadata=None, format=tarfile.PAX_FORMAT):
    path = tmp_path / "input.tar.gz"
    info = {"storage_type": "local"} if metadata is None else metadata.copy()
    if not legacy:
        info["backup_format"] = 2
    with tarfile.open(path, "w:gz", format=format) as archive:
        for name, payload in [
            ("metadata.json", json.dumps(info).encode()),
            ("db.sql" if legacy else "db.dump", b"database"),
            *members,
        ]:
            member = tarfile.TarInfo(name)
            if payload is None:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
    if not legacy:
        _sign(path)
    return path


def _sign(path):
    signature_path(path).write_bytes(sign_archive(path, signing_key(SECRET)))


def _restore(path, *, legacy=False, **limits):
    return backup.cmd_restore(
        argparse.Namespace(
            input=str(path),
            confirm=True,
            trust_legacy_unsigned=legacy,
            **{f"max_{key}": value for key, value in limits.items()},
        )
    )


def _forbid_database(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected archive reached a database subprocess")

    monkeypatch.setattr(backup.subprocess, "run", forbidden)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "limits, members, error",
    [
        ({"input_bytes": 1}, (), "compressed input"),
        ({"expanded_bytes": 1000}, (), "expanded byte"),
        ({"file_bytes": 4}, (), "single-file"),
        ({"headers": 2}, (("images/a", b"x"),), "header count"),
        ({"objects": 3}, (("images/a/b", b"x"),), "object count"),
        ({"path_depth": 2}, (("images/a/b", b"x"),), "path byte/depth"),
        ({"path_bytes": 20}, (("images/" + "a" * 30, b"x"),), "path byte/depth"),
        ({"json_bytes": 3}, (), "JSON byte"),
        ({"ratio": 1}, (("images/a", b"x" * 2000),), "compression ratio"),
    ],
)
def test_limits_reject_before_database(tmp_path, monkeypatch, capsys, legacy, limits, members, error):
    path = _archive(tmp_path, legacy=legacy, members=members)
    _forbid_database(monkeypatch)
    assert _restore(path, legacy=legacy, **limits) == 1
    assert error in capsys.readouterr().err
    assert not Path(settings.upload_dir).exists()


@pytest.mark.parametrize("name", ["../outside", "/outside", "images/../outside", "images/./x", "images//x", "other"])
def test_path_aliases_and_roots_rejected(tmp_path, monkeypatch, name):
    path = _archive(tmp_path, members=((name, b"x"),))
    _forbid_database(monkeypatch)
    assert _restore(path) == 1


@pytest.mark.parametrize(
    "members",
    [
        (("db.dump", b"again"),),
        (("images/a", b"x"), ("images/a", b"again")),
        (("images/a", b"x"), ("images/a/b", b"again")),
        (("images/a/", None), ("images/a", None)),
    ],
)
def test_duplicate_and_conflicting_targets_rejected(tmp_path, monkeypatch, members):
    path = _archive(tmp_path, members=members)
    _forbid_database(monkeypatch)
    assert _restore(path) == 1


def _header(name, *, size=0, type=tarfile.REGTYPE):
    member = tarfile.TarInfo(name)
    member.type = type
    member.size = size
    member.linkname = "images/target"
    return member.tobuf(format=tarfile.GNU_FORMAT)


def _raw(tmp_path, body):
    path = tmp_path / "raw.tar.gz"
    path.write_bytes(gzip.compress(body + b"\0" * 1024))
    _sign(path)
    return path


def _pax(key, value):
    record = f" {key}={value}\n".encode()
    length = len(record) + 1
    while len(str(length)) + len(record) != length:
        length = len(str(length)) + len(record)
    return str(length).encode() + record


def _extension(body, *, type=tarfile.XHDTYPE):
    return _header("extension", size=len(body), type=type) + body + b"\0" * (-len(body) % 512)


@pytest.mark.parametrize(
    "type",
    [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, tarfile.GNUTYPE_SPARSE],
)
def test_special_members_rejected(tmp_path, monkeypatch, type):
    path = _raw(tmp_path, _header("images/x", type=type))
    _forbid_database(monkeypatch)
    assert _restore(path) == 1


@pytest.mark.parametrize("type", [tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK])
def test_extension_size_rejected_before_stdlib_body_read(tmp_path, monkeypatch, type):
    path = _raw(tmp_path, _header("extension", size=100_000, type=type))
    _forbid_database(monkeypatch)
    assert _restore(path, extension_bytes=32) == 1


@pytest.mark.parametrize(
    "limits, error",
    [
        ({"extension_depth": 2}, "extension chain"),
        ({"headers": 2}, "header count"),
        ({"extensions_bytes": 10}, "cumulative tar extension"),
    ],
)
def test_extension_chains_and_hidden_headers_are_bounded(tmp_path, monkeypatch, capsys, limits, error):
    body = _extension(_pax("comment", "value")) * 4 + _header("db.dump")
    path = _raw(tmp_path, body)
    _forbid_database(monkeypatch)
    assert _restore(path, **limits) == 1
    assert error in capsys.readouterr().err


@pytest.mark.parametrize("key", ["GNU.sparse.map", "GNU.sparse.size", "GNU.sparse.major", "SCHILY.realsize"])
def test_sparse_pax_rejected_before_internal_sparse_handlers(tmp_path, monkeypatch, key):
    path = _raw(tmp_path, _extension(_pax(key, "1")) + _header("db.dump"))

    def forbidden(*args, **kwargs):
        pytest.fail("Sparse PAX reached stdlib processing")

    monkeypatch.setattr(tarfile.TarInfo, "_proc_pax", forbidden)
    _forbid_database(monkeypatch)
    assert _restore(path) == 1


@pytest.mark.parametrize("type", [tarfile.XHDTYPE, tarfile.XGLTYPE])
def test_pax_size_override_checked_before_file_body(tmp_path, monkeypatch, type):
    path = _raw(tmp_path, _extension(_pax("size", "999999"), type=type) + _header("db.dump"))
    _forbid_database(monkeypatch)
    assert _restore(path, file_bytes=1000) == 1


@pytest.mark.parametrize(
    "metadata, manifest",
    [
        ({"storage_type": []}, None),
        ({"storage_type": "other"}, None),
        ({"timestamp": {}}, None),
        ({"image_count": True}, None),
        ({"storage_type": "s3"}, b"[]"),
        ({"storage_type": "s3"}, b'{"bucket": {}}'),
        ({"storage_type": "s3"}, b"invalid"),
    ],
)
def test_metadata_and_manifest_validated_before_database(tmp_path, monkeypatch, metadata, manifest):
    members = () if manifest is None else (("manifest.json", manifest),)
    path = _archive(tmp_path, metadata=metadata, members=members)
    _forbid_database(monkeypatch)
    assert _restore(path) == 1


@pytest.mark.parametrize("format", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_unicode_long_paths_directories_and_regular_backup_restore(tmp_path, monkeypatch, format):
    relative = "gallery/" + "ä" * 60 + ".webp"
    path = _archive(tmp_path, format=format, members=(("images/", None), ("images/" + relative, b"image")))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", run)
    assert _restore(path) == 0
    assert len(calls) == 2
    assert (Path(settings.upload_dir) / relative).read_bytes() == b"image"


@pytest.mark.parametrize("kind", ["fifo", "symlink", "directory"])
def test_nonregular_input_fails_without_blocking(tmp_path, monkeypatch, kind):
    path = tmp_path / "nonregular"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        target = _archive(tmp_path, legacy=True)
        path.symlink_to(target)
    else:
        path.mkdir()
    _forbid_database(monkeypatch)
    assert _restore(path, legacy=True) == 1


def test_snapshot_bounds_actual_reads_if_source_grows_after_stat(tmp_path, monkeypatch):
    path = _archive(tmp_path, legacy=True)
    real_stat = os.fstat

    def stale_stat(fd):
        entry = real_stat(fd)
        values = list(entry)
        values[6] = 0
        assert stat.S_ISREG(entry.st_mode)
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", stale_stat)
    with pytest.raises(ValueError, match="compressed input"):
        with authenticated_snapshot(path, "", trust_legacy=True, budget=RestoreBudget(RestoreLimits(input_bytes=10))):
            pytest.fail("Growing source exceeded snapshot budget")


@pytest.mark.parametrize("mutation", ["crc", "truncated", "appended-tar", "deflate", "padding"])
def test_trailers_and_trailing_expansion_checked_before_database(tmp_path, monkeypatch, mutation):
    path = _archive(tmp_path)
    data = path.read_bytes()
    limits = {}
    if mutation == "crc":
        data = data[:-8] + bytes([data[-8] ^ 1]) + data[-7:]
    elif mutation == "truncated":
        data = data[:-5]
    elif mutation == "appended-tar":
        data += gzip.compress(_header("images/hidden") + b"\0" * 1024)
    elif mutation == "deflate":
        second = bytearray(gzip.compress(b"invalid"))
        second[10] = 0xFF  # Invalid deflate block type after a valid first gzip member.
        data += second
    else:
        data += gzip.compress(b"\0" * 20_000)
        limits["expanded_bytes"] = 15_000
    path.write_bytes(data)
    _sign(path)
    _forbid_database(monkeypatch)
    assert _restore(path, **limits) == 1


def test_large_appended_tail_cannot_dilute_ratio(tmp_path, monkeypatch, capsys):
    path = _archive(tmp_path, members=(("images/a", b"x" * 100_000),))
    with path.open("ab") as output:
        output.write(gzip.compress(os.urandom(100_000)))
    _sign(path)
    _forbid_database(monkeypatch)
    assert _restore(path, ratio=3) == 1
    assert "compression ratio" in capsys.readouterr().err


def test_deadline_checked_during_snapshot_before_output(tmp_path, monkeypatch):
    path = _archive(tmp_path, legacy=True)
    budget = RestoreBudget(RestoreLimits())
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise ValueError("deadline exceeded")

    monkeypatch.setattr(budget, "check", check)
    with pytest.raises(ValueError, match="deadline"):
        with authenticated_snapshot(path, "", trust_legacy=True, budget=budget):
            pytest.fail("Expired snapshot was exposed")


def test_deadline_checked_during_image_staging_before_database_restore(tmp_path, monkeypatch, capsys):
    path = _archive(tmp_path, members=(("images/a", b"image"),))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")

    def expired(*args, **kwargs):
        raise ValueError("deadline exceeded")

    monkeypatch.setattr(backup.subprocess, "run", run)
    monkeypatch.setattr(backup, "copy_bounded", expired)
    assert _restore(path) == 1
    assert len(calls) == 1  # Empty DB preflight only.
    assert "Could not stage images; database was not changed" in capsys.readouterr().err


def test_parser_reads_bounded_and_does_not_retain_member_cache(tmp_path, monkeypatch):
    path = _archive(tmp_path, members=tuple((f"images/{index}", b"x") for index in range(30)))
    real_read = gzip.GzipFile.read
    real_next = tarfile.TarFile.next

    def read(self, size=-1):
        assert 0 <= size <= CHUNK
        return real_read(self, size)

    def next(self):
        assert len(self.members) <= 1
        return real_next(self)

    monkeypatch.setattr(gzip.GzipFile, "read", read)
    monkeypatch.setattr(tarfile.TarFile, "next", next)
    destination = tmp_path / "extracted"
    destination.mkdir()
    with path.open("rb") as source:
        extract_archive(source, destination, RestoreBudget(RestoreLimits()))
    assert len(list((destination / "images").iterdir())) == 30


@pytest.mark.parametrize("field", [field.name for field in fields(RestoreLimits)])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_no_unlimited_or_nonfinite_limits(field, value):
    with pytest.raises(ValueError, match="positive and finite"):
        RestoreLimits(**{field: value})
