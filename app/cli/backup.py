"""Backup and restore CLI for Picture-Stage.

Creates/restores a .tar.gz archive containing:
  - metadata.json    — app version, timestamp, storage type
  - db.sql           — PostgreSQL dump (via pg_dump)
  - images/          — image files (LocalStorage only)
  - manifest.json    — S3 keys + bucket info (S3Storage only)

Usage:
  picture-stage backup [--output /path/to/backup.tar.gz]
  picture-stage restore --input /path/to/backup.tar.gz --confirm

Requires pg_dump/pg_restore in PATH (available in the Docker image).
Database connection is read from DATABASE_URL env var.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # noqa: S404
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

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


def _safe_tar_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract *tar* into *dest* with path-traversal protection."""
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        member_path = (dest / member.name).resolve()
        if not str(member_path).startswith(str(dest_resolved)):
            raise ValueError(
                f"Refusing to extract {member.name!r}: path traversal detected"
            )
    tar.extractall(dest, filter="data")  # noqa: S202


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def cmd_backup(args: argparse.Namespace) -> int:
    """Create a backup archive."""
    from app.config import settings  # lazy import — only needed at runtime

    db = _parse_database_url(settings.database_url)
    storage_type = settings.storage_backend  # "local" or "s3"
    upload_dir = settings.upload_dir

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = Path(f"picture-stage-backup-{ts}.tar.gz")

    print(f"[backup] Storage backend : {storage_type}")
    print(f"[backup] Database        : {db['host']}:{db['port']}/{db['dbname']}")
    print(f"[backup] Output          : {output_path}")

    # --- Verify DB connectivity ---
    try:
        subprocess.run(  # noqa: S603
            [
                "pg_isready",
                "-h", db["host"],
                "-p", db["port"],
                "-U", db["user"],
                "-d", db["dbname"],
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
        dump_path = tmp / "db.sql"
        try:
            subprocess.run(  # noqa: S603
                [
                    "pg_dump",
                    "-h", db["host"],
                    "-p", db["port"],
                    "-U", db["user"],
                    "-d", db["dbname"],
                    "--no-owner",
                    "--no-acl",
                    "-f", str(dump_path),
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
        print(f"[backup]   db.sql ({dump_path.stat().st_size:,} bytes)")

        # --- Metadata ---
        metadata: dict[str, object] = {
            "app_version": APP_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "storage_type": storage_type,
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
            with tarfile.open(output_path, "w:gz") as tar:
                tar.add(str(metadata_path), arcname="metadata.json")
                tar.add(str(dump_path), arcname="db.sql")

                if storage_type == "s3" and manifest_path is not None:
                    tar.add(str(manifest_path), arcname="manifest.json")
                elif storage_type == "local":
                    base = Path(upload_dir)
                    for rel in image_files:
                        full = base / rel
                        tar.add(str(full), arcname=f"images/{rel}")
        except Exception:
            # Clean up partial archive
            if output_path.exists():
                output_path.unlink()
            raise

        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"[backup] Done. Archive size: {size_mb:.1f} MB")

    return 0


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def cmd_restore(args: argparse.Namespace) -> int:
    """Restore from a backup archive."""
    from app.config import settings  # lazy import

    archive_path = Path(args.input)
    if not archive_path.is_file():
        print(f"[restore] ERROR: File not found: {archive_path}", file=sys.stderr)
        return 1

    if not args.confirm:
        print(
            "[restore] ERROR: Restore requires --confirm flag to prevent accidental data loss.",
            file=sys.stderr,
        )
        return 1

    db = _parse_database_url(settings.database_url)

    print(f"[restore] Archive : {archive_path}")
    print(f"[restore] Database: {db['host']}:{db['port']}/{db['dbname']}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # --- Extract and validate ---
        print("[restore] Extracting archive …")
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_tar_extract(tar, tmp)
        except (tarfile.TarError, ValueError) as exc:
            print(f"[restore] ERROR: {exc}", file=sys.stderr)
            return 1

        # Validate metadata
        metadata_path = tmp / "metadata.json"
        if not metadata_path.exists():
            print("[restore] ERROR: Archive is missing metadata.json — invalid backup.", file=sys.stderr)
            return 1

        metadata = json.loads(metadata_path.read_text())
        print(f"[restore] Backup from {metadata.get('timestamp', 'unknown')} "
              f"(app v{metadata.get('app_version', '?')}, storage: {metadata.get('storage_type', '?')})")

        dump_path = tmp / "db.sql"
        if not dump_path.exists():
            print("[restore] ERROR: Archive is missing db.sql — invalid backup.", file=sys.stderr)
            return 1

        # --- Warn if DB is non-empty ---
        try:
            result = subprocess.run(  # noqa: S603
                [
                    "psql",
                    "-h", db["host"],
                    "-p", db["port"],
                    "-U", db["user"],
                    "-d", db["dbname"],
                    "-t", "-A",
                    "-c", "SELECT count(*) FROM information_schema.tables "
                          "WHERE table_schema = 'public';",
                ],
                env=_pg_env(db),
                check=True,
                capture_output=True,
                text=True,
            )
            table_count = int(result.stdout.strip())
            if table_count > 0:
                print(f"[restore] WARNING: Target database has {table_count} existing table(s). "
                      "Restore will overwrite data.")
        except (FileNotFoundError, subprocess.CalledProcessError):
            print("[restore] WARNING: Could not check if database is empty (psql unavailable).")

        # --- Restore database ---
        print("[restore] Restoring database …")
        try:
            subprocess.run(  # noqa: S603
                [
                    "psql",
                    "-h", db["host"],
                    "-p", db["port"],
                    "-U", db["user"],
                    "-d", db["dbname"],
                    "-f", str(dump_path),
                ],
                env=_pg_env(db),
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            print("[restore] ERROR: psql not found. Install PostgreSQL client tools.", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as exc:
            print(f"[restore] ERROR: Database restore failed: {exc.stderr.decode()}", file=sys.stderr)
            return 1
        print("[restore]   Database restored.")

        # --- Restore images ---
        backup_storage = metadata.get("storage_type", "local")
        images_dir = tmp / "images"

        if backup_storage == "local" and images_dir.is_dir():
            upload_dir = Path(settings.upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            file_count = 0
            for src in images_dir.rglob("*"):
                if not src.is_file():
                    continue
                rel = src.relative_to(images_dir)
                dst = upload_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                # Validate destination is within upload_dir (belt-and-suspenders)
                if not str(dst.resolve()).startswith(str(upload_dir.resolve())):
                    print(f"[restore] WARNING: Skipping {rel} — path traversal detected", file=sys.stderr)
                    continue
                with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
                    while chunk := fsrc.read(65536):
                        fdst.write(chunk)
                file_count += 1
            print(f"[restore]   Restored {file_count} image file(s) to {upload_dir}")

        elif backup_storage == "s3":
            manifest_path_s3 = tmp / "manifest.json"
            if manifest_path_s3.exists():
                manifest = json.loads(manifest_path_s3.read_text())
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
        "--output", "-o",
        help="Output path for the archive (default: picture-stage-backup-<timestamp>.tar.gz)",
    )

    # -- restore --
    p_restore = sub.add_parser("restore", help="Restore from a backup archive")
    p_restore.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the backup archive",
    )
    p_restore.add_argument(
        "--confirm",
        action="store_true",
        help="Required flag to confirm restore (prevents accidental overwrites)",
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
