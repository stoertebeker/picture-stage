"""Backup and restore CLI for Picture-Stage.

Creates/restores a .tar.gz archive containing:
  - metadata.json    — app version, timestamp, storage type
  - db.dump          — PostgreSQL custom-format dump (via pg_dump)
  - images/          — image files (LocalStorage only)
  - manifest.json    — S3 keys + bucket info (S3Storage only)

Usage:
  picture-stage backup [--output /path/to/backup.tar.gz]
  picture-stage restore --input /path/to/backup.tar.gz --confirm

An adjacent .tar.gz.sig authenticates the complete archive using HMAC_SECRET_KEY.
Requires pg_dump, pg_restore and psql in PATH (available in the Docker image).
Database connection is read from DATABASE_URL env var.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zlib
from contextlib import ExitStack
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from app.cli.backup_auth import authenticated_snapshot, publish_backup, sign_archive, signing_key
from app.cli.restore_archive import RestoreBudget, RestoreLimits, copy_bounded, extract_archive, read_json

APP_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_database_url(url: str) -> dict[str, str]:
    """Parse DATABASE_URL into pg connection params.

    Accepts both asyncpg and psycopg2 schemes and normalises to plain
    ``postgresql://`` so the parsed components work with pg_dump/pg_restore.
    """
    normalised = url.replace("postgresql+asyncpg://", "postgresql://")
    normalised = normalised.replace("postgresql+psycopg2://", "postgresql://")
    parsed = urlparse(normalised)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/") or "postgres",
    }


def _pg_env(db: dict[str, str]) -> dict[str, str]:
    """Return a copy of os.environ with PGPASSWORD set (no password in argv)."""
    env = os.environ.copy()
    if db["password"]:
        env["PGPASSWORD"] = db["password"]
    return env


def _collect_local_images(upload_dir: str) -> list[str]:
    """Return relative paths of all files under *upload_dir*."""
    base = Path(upload_dir)
    if not base.is_dir():
        return []
    return sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())


def _safe_tar_extract(snapshot: BinaryIO, dest: Path, budget: RestoreBudget) -> None:
    """Apply shared restore budgets at the authenticated compressed boundary."""
    extract_archive(snapshot, dest, budget)


def _image_destination(upload_dir: Path, relative_path: Path) -> Path:
    """Validate a restore target before staging and again before publication."""
    destination = upload_dir / relative_path
    if not destination.resolve().is_relative_to(upload_dir.resolve()):
        raise ValueError(f"Refusing image destination {relative_path}: path traversal detected")
    if destination.exists() and not destination.is_file():
        raise ValueError(f"Image destination is not a file: {relative_path}")
    for parent in destination.parents:
        if parent == upload_dir:
            break
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"Image destination parent is not a directory: {relative_path}")
    return destination


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def cmd_backup(args: argparse.Namespace) -> int:
    """Create a backup archive."""
    from app.config import settings  # lazy import — only needed at runtime

    db = _parse_database_url(settings.database_url)
    storage_type = settings.storage_backend  # "local" or "s3"
    upload_dir = settings.upload_dir

    try:
        key = signing_key(settings.hmac_secret_key)
    except ValueError as exc:
        print(f"[backup] ERROR: {exc}", file=sys.stderr)
        return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_path = Path(f"picture-stage-backup-{ts}.tar.gz")

    print(f"[backup] Storage backend : {storage_type}")
    print(f"[backup] Database        : {db['host']}:{db['port']}/{db['dbname']}")
    print(f"[backup] Output          : {output_path}")

    # --- Verify DB connectivity ---
    try:
        subprocess.run(
            [
                "pg_isready",
                "-h",
                db["host"],
                "-p",
                db["port"],
                "-U",
                db["user"],
                "-d",
                db["dbname"],
            ],
            env=_pg_env(db),
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print("[backup] WARNING: pg_isready not found, skipping connectivity check")
    except subprocess.CalledProcessError:
        print("[backup] ERROR: Cannot connect to database. Aborting.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # --- pg_dump ---
        print("[backup] Dumping database …")
        dump_path = tmp / "db.dump"
        try:
            subprocess.run(
                [
                    "pg_dump",
                    "-h",
                    db["host"],
                    "-p",
                    db["port"],
                    "-U",
                    db["user"],
                    "-d",
                    db["dbname"],
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "-f",
                    str(dump_path),
                ],
                env=_pg_env(db),
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            print("[backup] ERROR: pg_dump not found. Install PostgreSQL client tools.", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as exc:
            print(f"[backup] ERROR: pg_dump failed: {exc.stderr.decode()}", file=sys.stderr)
            return 1
        print(f"[backup]   db.dump ({dump_path.stat().st_size:,} bytes)")

        # --- Metadata ---
        metadata: dict[str, object] = {
            "app_version": APP_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
            "storage_type": storage_type,
            "backup_format": 2,
        }

        # --- Images / manifest ---
        image_files: list[str] = []
        manifest_path: Path | None = None
        if storage_type == "local":
            image_files = _collect_local_images(upload_dir)
            print(f"[backup] Collecting {len(image_files)} image file(s) from {upload_dir} …")
            metadata["image_count"] = len(image_files)
        elif storage_type == "s3":
            print("[backup] S3 backend — writing manifest (images are stored externally)")
            manifest = {
                "bucket": settings.s3_bucket_name,
                "endpoint": settings.s3_endpoint_url,
                "region": settings.s3_region,
                "note": "Image files are stored in S3 and NOT included in this archive. "
                "Ensure the S3 bucket is backed up separately.",
            }
            manifest_path = tmp / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2))

        metadata_path = tmp / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2))

        # --- Build tar.gz ---
        print(f"[backup] Creating archive {output_path} …")
        try:
            # Stage on the destination filesystem so exclusive publication
            # never exposes partial archives or overwrites an existing pair.
            with tempfile.TemporaryDirectory(prefix=".backup-", dir=output_path.parent) as publication:
                staged_archive = Path(publication) / "archive.tar.gz"
                with tarfile.open(staged_archive, "w:gz") as tar:
                    tar.add(str(metadata_path), arcname="metadata.json")
                    tar.add(str(dump_path), arcname="db.dump")

                    if storage_type == "s3" and manifest_path is not None:
                        tar.add(str(manifest_path), arcname="manifest.json")
                    elif storage_type == "local":
                        base = Path(upload_dir)
                        for rel in image_files:
                            full = base / rel
                            tar.add(str(full), arcname=f"images/{rel}")
                publish_backup(staged_archive, output_path, sign_archive(staged_archive, key))
        except (OSError, tarfile.TarError) as exc:
            print(f"[backup] ERROR: Could not publish signed backup: {exc}", file=sys.stderr)
            return 1

        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"[backup] Done. Archive size: {size_mb:.1f} MB")
        print(f"[backup] Keep {output_path}.sig with the archive and retain the original HMAC_SECRET_KEY separately.")

    return 0


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore from a backup archive."""
    from app.config import settings  # lazy import

    archive_path = Path(args.input)
    if not args.confirm:
        print(
            "[restore] ERROR: Restore requires --confirm flag to prevent accidental data loss.",
            file=sys.stderr,
        )
        return 1

    try:
        limits = RestoreLimits(
            **{
                field.name: getattr(args, f"max_{field.name}", getattr(RestoreLimits(), field.name))
                for field in fields(RestoreLimits)
            }
        )
        budget = RestoreBudget(limits)
    except ValueError as exc:
        print(f"[restore] ERROR: {exc}", file=sys.stderr)
        return 1
    db = _parse_database_url(settings.database_url)

    print(f"[restore] Archive : {archive_path}")
    print(f"[restore] Database: {db['host']}:{db['port']}/{db['dbname']}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # --- Extract and validate ---
        print("[restore] Extracting archive …")
        try:
            with authenticated_snapshot(
                archive_path,
                settings.hmac_secret_key,
                trust_legacy=getattr(args, "trust_legacy_unsigned", False),
                budget=budget,
            ) as (snapshot, authenticated):
                _safe_tar_extract(snapshot, tmp, budget)
        except (OSError, EOFError, tarfile.TarError, ValueError, zlib.error) as exc:
            print(f"[restore] ERROR: {exc}", file=sys.stderr)
            return 1

        # Validate metadata
        metadata_path = tmp / "metadata.json"
        if not metadata_path.exists():
            print("[restore] ERROR: Archive is missing metadata.json — invalid backup.", file=sys.stderr)
            return 1

        try:
            metadata = read_json(metadata_path, budget)
            manifest_path = tmp / "manifest.json"
            manifest = read_json(manifest_path, budget, manifest=True) if manifest_path.exists() else None
        except (OSError, ValueError) as exc:
            print(f"[restore] ERROR: Invalid metadata.json: {exc}", file=sys.stderr)
            return 1
        print(
            f"[restore] Backup from {metadata.get('timestamp', 'unknown')} "
            f"(app v{metadata.get('app_version', '?')}, storage: {metadata.get('storage_type', '?')})"
        )

        legacy = not authenticated
        dump_path = tmp / ("db.sql" if legacy else "db.dump")
        other_dump = tmp / ("db.dump" if legacy else "db.sql")
        expected_format = metadata.get("backup_format") is None if legacy else metadata.get("backup_format") == 2
        if not expected_format or not dump_path.is_file() or other_dump.exists():
            print("[restore] ERROR: Archive has an invalid or unsupported database backup format.", file=sys.stderr)
            return 1
        if legacy:
            print(
                "[restore] WARNING: Trusting an unsigned legacy backup. Its SQL can execute commands as this process.",
                file=sys.stderr,
            )

        # Full pg_dump archives require an empty target. Never imply that
        # feeding CREATE/COPY statements into an existing schema replaces it.
        psql = [
            "psql",
            "-X",
            "--no-password",
            "--set",
            "ON_ERROR_STOP=on",
            "-h",
            db["host"],
            "-p",
            db["port"],
            "-U",
            db["user"],
            "-d",
            db["dbname"],
        ]
        try:
            result = subprocess.run(
                [
                    *psql,
                    "-t",
                    "-A",
                    "-c",
                    "SELECT count(*) FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema';",
                ],
                env=_pg_env(db),
                stdin=subprocess.DEVNULL,
                check=True,
                capture_output=True,
                text=True,
            )
            if int(result.stdout.strip()) != 0:
                print(
                    "[restore] ERROR: Target database is not empty. Restore requires an empty database; "
                    "existing-schema and data-only restores are not supported. No data was changed.",
                    file=sys.stderr,
                )
                return 1
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            print(f"[restore] ERROR: Could not verify an empty target database: {exc}", file=sys.stderr)
            return 1

        backup_storage = metadata.get("storage_type", "local")
        images_dir = tmp / "images"
        with ExitStack() as staging:
            # Stage on the upload filesystem before changing the DB. SQL
            # failures leave existing images untouched; file replacement is
            # atomic per image, including when upload_dir is a volume mount.
            upload_dir = Path(settings.upload_dir)
            staged_images: Path | None = None
            image_paths: list[Path] = []
            if backup_storage == "local" and images_dir.is_dir():
                try:
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    staged_images = Path(
                        staging.enter_context(tempfile.TemporaryDirectory(prefix=".restore-", dir=upload_dir))
                    )
                    for src in images_dir.rglob("*"):
                        budget.check()
                        if not src.is_file():
                            continue
                        rel = src.relative_to(images_dir)
                        _image_destination(upload_dir, rel)
                        staged = staged_images / rel
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        with src.open("rb") as source, staged.open("xb") as output:
                            copy_bounded(source, output, budget, expected=src.stat().st_size)
                        image_paths.append(rel)
                except (OSError, ValueError) as exc:
                    print(f"[restore] ERROR: Could not stage images; database was not changed: {exc}", file=sys.stderr)
                    return 1

            try:
                budget.check()
            except ValueError as exc:
                print(f"[restore] ERROR: {exc}; database was not changed", file=sys.stderr)
                return 1
            print("[restore] Restoring database …")
            restore_command = (
                [*psql, "--single-transaction", "-f", str(dump_path)]
                if legacy
                else [
                    "pg_restore",
                    "--no-password",
                    "-h",
                    db["host"],
                    "-p",
                    db["port"],
                    "-U",
                    db["user"],
                    "-d",
                    db["dbname"],
                    "--format=custom",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-acl",
                    str(dump_path),
                ]
            )
            try:
                subprocess.run(
                    restore_command,
                    env=_pg_env(db),
                    stdin=subprocess.DEVNULL,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
                print(f"[restore] ERROR: Database restore failed; images were not changed: {detail}", file=sys.stderr)
                return 1
            print("[restore]   Database restored.")

            if staged_images is not None:
                try:
                    for rel in image_paths:
                        dst = _image_destination(upload_dir, rel)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(staged_images / rel, dst)
                except (OSError, ValueError) as exc:
                    print(
                        f"[restore] ERROR: Database was restored, but image restoration is incomplete: {exc}. "
                        "Keep the application stopped and recover the images from the backup before using it.",
                        file=sys.stderr,
                    )
                    return 1
                print(f"[restore]   Restored {len(image_paths)} image file(s) to {upload_dir}")

        if backup_storage == "s3":
            if manifest is not None:
                print(f"[restore]   S3 backend — images are in bucket {manifest.get('bucket', '?')}.")
                print("[restore]   Ensure the S3 bucket is intact. This archive does not contain image files.")
            else:
                print("[restore]   S3 storage detected but no manifest.json found.")

    print("[restore] Done.")
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for ``picture-stage`` console script."""
    parser = argparse.ArgumentParser(
        prog="picture-stage",
        description="Picture-Stage backup and restore CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # -- backup --
    p_backup = sub.add_parser("backup", help="Create a backup archive")
    p_backup.add_argument(
        "--output",
        "-o",
        help="Output path for the archive (default: picture-stage-backup-<timestamp>.tar.gz)",
    )

    # -- restore --
    p_restore = sub.add_parser("restore", help="Restore from a backup archive")
    p_restore.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to the backup archive",
    )
    p_restore.add_argument(
        "--trust-legacy-unsigned",
        action="store_true",
        help="Trust only known, operator-owned old unsigned backups; their SQL can execute commands as this process",
    )
    p_restore.add_argument(
        "--confirm",
        action="store_true",
        help="Required flag to confirm restore (prevents accidental overwrites)",
    )

    for field in fields(RestoreLimits):
        default = getattr(RestoreLimits(), field.name)
        p_restore.add_argument(
            f"--max-{field.name.replace('_', '-')}",
            type=float if field.name == "seconds" else int,
            default=default,
            help=f"Positive finite restore {field.name.replace('_', ' ')} limit (default: {default})",
        )

    args = parser.parse_args()

    if args.command == "backup":
        sys.exit(cmd_backup(args))
    elif args.command == "restore":
        sys.exit(cmd_restore(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
