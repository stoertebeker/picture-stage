"""Restore regressions against disposable databases, never the app schema.

DATABASE_URL must name a dedicated test database whose role can CREATE DATABASE.
Each test creates and drops its own uniquely named database. PostgreSQL client
tools are installed in CI and the application image.
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from app.cli import backup
from app.cli.backup_auth import sign_archive, signature_path, signing_key
from app.config import settings

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("psql", "pg_dump", "pg_restore")),
    reason="PostgreSQL client tools required",
)

IMAGE_PATH = "gallery/original.webp"


def _sql(url: str, statement: str) -> str:
    db = backup._parse_database_url(url)
    result = subprocess.run(  # noqa: S603 -- isolated test DB, no shell
        [
            shutil.which("psql"),
            "-X",
            "--no-password",
            "-v",
            "ON_ERROR_STOP=on",
            "-h",
            db["host"],
            "-p",
            db["port"],
            "-U",
            db["user"],
            "-d",
            db["dbname"],
            "-t",
            "-A",
            "-c",
            statement,
        ],
        env=backup._pg_env(db),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


@dataclass
class RestoreDatabase:
    base_url: str
    url: str
    name: str

    def sql(self, statement: str) -> str:
        return _sql(self.url, statement)

    def reset(self) -> None:
        _sql(self.base_url, f'DROP DATABASE "{self.name}" WITH (FORCE)')
        _sql(self.base_url, f'CREATE DATABASE "{self.name}" TEMPLATE template0')


@pytest.fixture
def database(monkeypatch, tmp_path):
    base_url = settings.database_url
    name = f"restore_test_{uuid.uuid4().hex}"
    parsed = urlsplit(base_url)
    url = urlunsplit(parsed._replace(path=f"/{name}"))
    _sql(base_url, f'CREATE DATABASE "{name}" TEMPLATE template0')
    try:
        monkeypatch.setattr(settings, "database_url", url)
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(settings, "storage_backend", "local")
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
        monkeypatch.setattr(settings, "hmac_secret_key", "restore-tests-only-key-with-at-least-32-bytes")
        yield RestoreDatabase(base_url, url, name)
    finally:
        _sql(base_url, f'DROP DATABASE "{name}" WITH (FORCE)')


def _archive(tmp_path: Path, sql: str, *, storage: str = "local") -> Path:
    path = tmp_path / "backup.tar.gz"
    members = {
        "metadata.json": json.dumps({"storage_type": storage, "app_version": "0.1.0"}).encode(),
        "db.sql": sql.encode(),
    }
    if storage == "local":
        members[f"images/{IMAGE_PATH}"] = b"backup image bytes"
    else:
        members["manifest.json"] = json.dumps({"bucket": "test-bucket"}).encode()
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return path


def _restore(path: Path, *, legacy: bool = True) -> int:
    return backup.cmd_restore(argparse.Namespace(input=str(path), confirm=True, trust_legacy_unsigned=legacy))


def _existing_image() -> Path:
    path = Path(settings.upload_dir) / IMAGE_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original image bytes")
    return path


def test_sql_failure_rolls_back_schema_and_rows_and_preserves_images(database, tmp_path, monkeypatch, capsys):
    image = _existing_image()
    # A startup file must not override the restore's fail-fast policy.
    startup = tmp_path / "psqlrc"
    startup.write_text("\\set ON_ERROR_STOP off\n")
    monkeypatch.setenv("PSQLRC", str(startup))
    archive = _archive(
        tmp_path,
        "CREATE TABLE restored (id integer PRIMARY KEY);\n"
        "INSERT INTO restored VALUES (1);\n"
        "INSERT INTO restored VALUES (1);\n"
        "CREATE TABLE should_not_exist (id integer);\n",
    )
    assert _restore(archive) == 1
    assert database.sql("SELECT to_regclass('restored') IS NULL") == "t"
    assert database.sql("SELECT to_regclass('should_not_exist') IS NULL") == "t"
    assert image.read_bytes() == b"original image bytes"
    assert not list(Path(settings.upload_dir).glob(".restore-*"))
    output = capsys.readouterr()
    assert "Database restore failed" in output.err
    assert "duplicate key" in output.err
    assert "Database restored." not in output.out
    assert "[restore] Done." not in output.out


@pytest.mark.parametrize(
    "existing",
    [
        "CREATE TABLE preserved (id integer); INSERT INTO preserved VALUES (42)",
        "CREATE SCHEMA other; CREATE SEQUENCE other.preserved",
        "CREATE VIEW preserved AS SELECT 42 AS id",
    ],
)
def test_existing_database_is_rejected_without_changing_data_or_images(database, tmp_path, capsys, existing):
    database.sql(existing)
    before = database.sql(
        "SELECT string_agg(n.nspname || '.' || c.relname, ',' ORDER BY n.nspname, c.relname) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"
    )
    image = _existing_image()
    archive = _archive(tmp_path, "CREATE TABLE should_not_exist (id integer)")
    assert _restore(archive) == 1
    assert database.sql("SELECT to_regclass('should_not_exist') IS NULL") == "t"
    assert (
        database.sql(
            "SELECT string_agg(n.nspname || '.' || c.relname, ',' ORDER BY n.nspname, c.relname) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"
        )
        == before
    )
    if "other" not in existing:
        assert database.sql("SELECT id FROM preserved") == "42"
    assert image.read_bytes() == b"original image bytes"
    assert "Target database is not empty" in capsys.readouterr().err


def test_full_backup_roundtrip_restores_admin_guard_and_images(database, tmp_path, capsys):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    database.sql(
        "SELECT set_config('picture_stage.admin_insert', 'bootstrap', true); "
        "INSERT INTO users (id, email, password_hash, status) "
        "VALUES ('00000000-0000-0000-0000-000000000001', 'admin@example.com', 'hash', 'admin')"
    )
    image = _existing_image()
    archive = tmp_path / "roundtrip.tar.gz"
    assert backup.cmd_backup(argparse.Namespace(output=str(archive))) == 0
    assert signature_path(archive).is_file()
    with tarfile.open(archive) as contents:
        assert "db.dump" in contents.getnames()
        assert "db.sql" not in contents.getnames()
    database.reset()
    image.write_bytes(b"changed bytes")
    assert _restore(archive, legacy=False) == 0
    assert database.sql("SELECT email FROM users WHERE status = 'admin'") == "admin@example.com"
    assert database.sql("SELECT completed FROM setup_state WHERE id = 1") == "t"
    assert database.sql("SELECT version_num FROM alembic_version") == "0018"
    with pytest.raises(subprocess.CalledProcessError, match="exit status"):
        database.sql(
            "INSERT INTO users (id, email, password_hash, status) "
            "VALUES ('00000000-0000-0000-0000-000000000002', 'blocked@example.com', 'hash', 'admin')"
        )
    assert image.read_bytes() == b"original image bytes"
    assert not list(Path(settings.upload_dir).glob(".restore-*"))
    assert "[restore] Done." in capsys.readouterr().out


def test_preflight_failure_aborts_without_restoring(database, tmp_path, monkeypatch, capsys):
    archive = _archive(tmp_path, "CREATE TABLE should_not_exist (id integer)")
    image = _existing_image()

    def offline(*args, **kwargs):
        raise OSError("offline")

    with monkeypatch.context() as patch:
        patch.setattr(backup.subprocess, "run", offline)
        assert _restore(archive) == 1
    assert database.sql("SELECT to_regclass('should_not_exist') IS NULL") == "t"
    assert image.read_bytes() == b"original image bytes"
    assert "Could not verify an empty target database" in capsys.readouterr().err


def test_image_staging_failure_leaves_database_and_images_unchanged(database, tmp_path, monkeypatch, capsys):
    archive = _archive(tmp_path, "CREATE TABLE should_not_exist (id integer)")
    image = _existing_image()

    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "copy_bounded", disk_full)
    assert _restore(archive) == 1
    assert database.sql("SELECT to_regclass('should_not_exist') IS NULL") == "t"
    assert image.read_bytes() == b"original image bytes"
    assert not list(Path(settings.upload_dir).glob(".restore-*"))
    assert "Could not stage images; database was not changed" in capsys.readouterr().err


def test_image_destination_conflict_is_rejected_before_sql(database, tmp_path, capsys):
    Path(settings.upload_dir).mkdir()
    (Path(settings.upload_dir) / "gallery").write_bytes(b"not a directory")
    archive = _archive(tmp_path, "CREATE TABLE should_not_exist (id integer)")
    assert _restore(archive) == 1
    assert database.sql("SELECT to_regclass('should_not_exist') IS NULL") == "t"
    assert (Path(settings.upload_dir) / "gallery").read_bytes() == b"not a directory"
    assert "Could not stage images" in capsys.readouterr().err


def test_image_publication_failure_reports_committed_database(database, tmp_path, monkeypatch, capsys):
    archive = _archive(tmp_path, "CREATE TABLE restored (id integer)")
    image = _existing_image()

    def permission_denied(*args, **kwargs):
        raise PermissionError("read-only destination")

    monkeypatch.setattr(backup.os, "replace", permission_denied)
    assert _restore(archive) == 1
    assert database.sql("SELECT to_regclass('restored') IS NOT NULL") == "t"
    assert image.read_bytes() == b"original image bytes"
    assert not list(Path(settings.upload_dir).glob(".restore-*"))
    output = capsys.readouterr()
    assert "Database was restored, but image restoration is incomplete" in output.err
    assert "[restore] Done." not in output.out


def test_s3_restore_keeps_manifest_semantics(database, tmp_path, capsys):
    archive = _archive(tmp_path, "CREATE TABLE restored (id integer)", storage="s3")
    assert _restore(archive) == 0
    assert database.sql("SELECT to_regclass('restored') IS NOT NULL") == "t"
    assert not Path(settings.upload_dir).exists()
    assert "images are in bucket test-bucket" in capsys.readouterr().out


def test_signed_custom_restore_failure_rolls_back_schema_data_and_images(database, tmp_path, monkeypatch, capsys):
    # The source can hold these rows; restoring with the different client
    # application name fails its CHECK during COPY, after CREATE TABLE.
    database.sql(
        "CREATE TABLE restored (id integer CHECK (current_setting('application_name') <> 'pg_restore')); "
        "INSERT INTO restored VALUES (1)"
    )
    image = _existing_image()
    archive = tmp_path / "custom-failure.tar.gz"
    assert backup.cmd_backup(argparse.Namespace(output=str(archive))) == 0
    database.reset()
    image.write_bytes(b"current image")
    monkeypatch.setenv("PGAPPNAME", "pg_restore")
    assert _restore(archive, legacy=False) == 1
    assert database.sql("SELECT to_regclass('restored') IS NULL") == "t"
    assert image.read_bytes() == b"current image"
    assert not list(Path(settings.upload_dir).glob(".restore-*"))
    assert "Database restore failed" in capsys.readouterr().err


def test_unsigned_meta_command_archive_is_rejected_without_marker_or_changes(database, tmp_path, capsys):
    marker = tmp_path / "psql-meta-marker"
    archive = _archive(tmp_path, f"\\! touch {marker}\nCREATE TABLE should_not_exist (id integer);\n")
    image = _existing_image()
    assert _restore(archive, legacy=False) == 1
    assert not marker.exists()
    assert database.sql("SELECT to_regclass('should_not_exist') IS NULL") == "t"
    assert image.read_bytes() == b"original image bytes"
    assert "signature is missing" in capsys.readouterr().err


def test_signed_custom_payload_cannot_invoke_psql_meta_commands(database, tmp_path, capsys):
    marker = tmp_path / "psql-meta-marker"
    archive = tmp_path / "fake-custom.tar.gz"
    members = {
        "metadata.json": json.dumps({"storage_type": "local", "backup_format": 2}).encode(),
        "db.dump": f"\\! touch {marker}\nCREATE TABLE should_not_exist (id integer);\n".encode(),
    }
    with tarfile.open(archive, "w:gz") as contents:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            contents.addfile(info, io.BytesIO(payload))
    signature_path(archive).write_bytes(sign_archive(archive, signing_key(settings.hmac_secret_key)))
    assert _restore(archive, legacy=False) == 1
    assert not marker.exists()
    assert database.sql("SELECT to_regclass('should_not_exist') IS NULL") == "t"
    assert "Database restore failed" in capsys.readouterr().err
