"""Backup authentication at the CLI boundary, without a database or network."""

import argparse
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from app.cli import backup
from app.cli.backup_auth import sign_archive, signature_path, signing_key
from app.config import settings

TEST_SECRET = "backup-auth-tests-only-secret-at-least-32-bytes"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "hmac_secret_key", TEST_SECRET)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "storage_backend", "local")


def _archive(tmp_path, *, signed=True, legacy=False, payload=b"PGDMP-source"):
    path = tmp_path / "backup.tar.gz"
    metadata = {"storage_type": "local"}
    if not legacy:
        metadata["backup_format"] = 2
    members = {"metadata.json": json.dumps(metadata).encode(), "db.sql" if legacy else "db.dump": payload}
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    if signed:
        signature_path(path).write_bytes(sign_archive(path, signing_key(TEST_SECRET)))
    return path


def _restore(path, *, trust=False):
    return backup.cmd_restore(argparse.Namespace(input=str(path), confirm=True, trust_legacy_unsigned=trust))


def _no_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Untrusted archive reached extraction or a database subprocess")

    monkeypatch.setattr(backup, "_safe_tar_extract", forbidden)
    monkeypatch.setattr(backup.subprocess, "run", forbidden)


@pytest.mark.parametrize("legacy", [False, True])
def test_unsigned_archive_is_rejected_before_extraction_and_database(tmp_path, monkeypatch, capsys, legacy):
    archive = _archive(tmp_path, signed=False, legacy=legacy, payload=b"\\! echo untrusted-meta-command\n")
    _no_effects(monkeypatch)
    assert _restore(archive) == 1
    assert "signature is missing" in capsys.readouterr().err
    assert not Path(settings.upload_dir).exists()


@pytest.mark.parametrize("trust", [False, True])
@pytest.mark.parametrize("change", ["append", "truncate", "replace", "wrong-key"])
def test_modified_or_wrong_key_archive_cannot_bypass_signature(tmp_path, monkeypatch, capsys, trust, change):
    archive = _archive(tmp_path)
    if change == "append":
        with archive.open("ab") as destination:
            destination.write(b"tampered")
    elif change == "truncate":
        archive.write_bytes(archive.read_bytes()[:20])
    elif change == "replace":
        archive.write_bytes(b"\\! echo substituted-meta-command\n")
    else:
        monkeypatch.setattr(settings, "hmac_secret_key", "different-strong-secret-with-more-than-32-bytes")
    _no_effects(monkeypatch)
    assert _restore(archive, trust=trust) == 1
    assert "signature is invalid" in capsys.readouterr().err


@pytest.mark.parametrize("signature", [b"", b"broken", b"0" * 64, b"0" * 4096])
def test_malformed_present_signature_is_not_legacy(tmp_path, monkeypatch, signature):
    archive = _archive(tmp_path, legacy=True)
    signature_path(archive).write_bytes(signature)
    _no_effects(monkeypatch)
    assert _restore(archive, trust=True) == 1


@pytest.mark.parametrize("kind", ["directory", "dangling-symlink", "symlink", "fifo"])
def test_non_regular_signature_is_not_legacy(tmp_path, monkeypatch, kind):
    archive = _archive(tmp_path, signed=False, legacy=True)
    sidecar = signature_path(archive)
    if kind == "directory":
        sidecar.mkdir()
    elif kind == "fifo":
        import os

        os.mkfifo(sidecar)
    else:
        target = tmp_path / "target"
        if kind == "symlink":
            target.write_bytes(sign_archive(archive, signing_key(TEST_SECRET)))
        sidecar.symlink_to(target)
    _no_effects(monkeypatch)
    assert _restore(archive, trust=True) == 1


@pytest.mark.parametrize("secret", ["", "CHANGE_ME", "short", " " * 64])
def test_unsafe_key_fails_closed_for_backup_and_signed_restore(tmp_path, monkeypatch, capsys, secret):
    archive = _archive(tmp_path)
    monkeypatch.setattr(settings, "hmac_secret_key", secret)
    _no_effects(monkeypatch)
    assert _restore(archive, trust=True) == 1
    assert backup.cmd_backup(argparse.Namespace(output=str(tmp_path / "new.tar.gz"))) == 1
    assert "HMAC_SECRET_KEY" in capsys.readouterr().err


def test_signed_plain_sql_never_reaches_psql_even_with_trust(tmp_path, monkeypatch):
    archive = _archive(tmp_path, legacy=True, payload=b"\\! echo meta-command\n")

    def forbidden(*args, **kwargs):
        pytest.fail("Signed plain SQL reached database client")

    monkeypatch.setattr(backup.subprocess, "run", forbidden)
    assert _restore(archive, trust=True) == 1


def test_unsigned_new_format_cannot_use_legacy_exception(tmp_path, monkeypatch):
    archive = _archive(tmp_path, signed=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Unsigned new format reached database client")

    monkeypatch.setattr(backup.subprocess, "run", forbidden)
    assert _restore(archive, trust=True) == 1


def test_explicit_trusted_old_backup_restores_without_unused_key(tmp_path, monkeypatch, capsys):
    archive = _archive(tmp_path, signed=False, legacy=True, payload=b"SELECT 1;\n")
    monkeypatch.setattr(settings, "hmac_secret_key", "")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if "-f" in command:
            assert Path(command[-1]).read_bytes() == b"SELECT 1;\n"
        return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", run)
    assert _restore(archive, trust=True) == 0
    assert len(commands) == 2
    assert all(command[0] == "psql" for command in commands)
    assert "SQL can execute commands" in capsys.readouterr().err


def test_restore_uses_authenticated_snapshot_after_original_path_is_replaced(tmp_path, monkeypatch):
    archive = _archive(tmp_path)
    real_open = tarfile.open
    commands = []

    def replace_original(*args, **kwargs):
        archive.write_bytes(b"replacement must never be parsed")
        return real_open(*args, **kwargs)

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "pg_restore":
            assert Path(command[-1]).read_bytes() == b"PGDMP-source"
        return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")

    monkeypatch.setattr(backup.tarfile, "open", replace_original)
    monkeypatch.setattr(backup.subprocess, "run", run)
    assert _restore(archive) == 0
    assert commands[0][0] == "psql" and "-c" in commands[0] and "-f" not in commands[0]
    assert commands[1][0] == "pg_restore"
    assert {"--exit-on-error", "--single-transaction", "--no-owner", "--no-acl", "--format=custom"} <= set(commands[1])


def _fake_dump(monkeypatch):
    def run(command, **kwargs):
        if command[0] == "pg_dump":
            assert "--format=custom" in command
            Path(command[-1]).write_bytes(b"PGDMP-source")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(backup.subprocess, "run", run)


def test_backup_publishes_verified_pair_with_no_key_in_archive(tmp_path, monkeypatch):
    _fake_dump(monkeypatch)
    archive = tmp_path / "published.tar.gz"
    assert backup.cmd_backup(argparse.Namespace(output=str(archive))) == 0
    assert signature_path(archive).read_bytes() == sign_archive(archive, signing_key(TEST_SECRET))
    with tarfile.open(archive) as contents:
        assert set(contents.getnames()) == {"metadata.json", "db.dump"}
        assert TEST_SECRET.encode() not in contents.extractfile("metadata.json").read()
    assert not list(tmp_path.glob(".backup-*"))


@pytest.mark.parametrize("existing", ["archive", "signature", "both"])
def test_backup_never_overwrites_existing_output_pair(tmp_path, monkeypatch, existing):
    _fake_dump(monkeypatch)
    archive = tmp_path / "published.tar.gz"
    sidecar = signature_path(archive)
    if existing in ("archive", "both"):
        archive.write_bytes(b"existing archive")
    if existing in ("signature", "both"):
        sidecar.write_bytes(b"existing signature")
    assert backup.cmd_backup(argparse.Namespace(output=str(archive))) == 1
    assert archive.read_bytes() == b"existing archive" if existing in ("archive", "both") else not archive.exists()
    assert sidecar.read_bytes() == b"existing signature" if existing in ("signature", "both") else not sidecar.exists()
    assert not list(tmp_path.glob(".backup-*"))


def test_backup_partial_publication_failure_removes_only_new_sidecar(tmp_path, monkeypatch):
    _fake_dump(monkeypatch)
    archive = tmp_path / "published.tar.gz"
    real_link = backup.os.link

    def failing_archive_link(source, destination):
        if destination == archive:
            raise OSError("disk full")
        real_link(source, destination)

    monkeypatch.setattr(backup.os, "link", failing_archive_link)
    assert backup.cmd_backup(argparse.Namespace(output=str(archive))) == 1
    assert not archive.exists()
    assert not signature_path(archive).exists()
    assert not list(tmp_path.glob(".backup-*"))
