import hashlib
import json
import logging
import os
import shutil
import stat
import struct
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from sqlite3 import Connection, OperationalError, Row
from typing import BinaryIO

from .config import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_PATH_BYTES,
    MAX_SITE_BYTES,
    MAX_SITE_FILES,
)
from .exceptions import BadRequest, Conflict, Forbidden, NotFound, PayloadTooLarge


_ARCHIVE_CHUNK_BYTES = 1024 * 1024
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_OPERATIONS_DIR = ".operations"
# Per-process locks: deploy/delete mutual exclusion (and the journal design
# built on it) assumes a single server process.
_SITE_LOCKS = tuple(threading.Lock() for _ in range(64))
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeploymentLimits:
    max_archive_bytes: int = MAX_ARCHIVE_BYTES
    max_site_bytes: int = MAX_SITE_BYTES
    max_entries: int = MAX_SITE_FILES
    max_path_bytes: int = MAX_ARCHIVE_PATH_BYTES


@dataclass
class SiteRecord:
    name: str
    owner_id: int | None
    size_bytes: int
    created_at: str


@dataclass
class FileEntry:
    path: str
    size_bytes: int
    is_dir: bool
    depth: int


class SiteStore:
    def __init__(
        self,
        conn: Connection,
        sites_dir: Path,
        limits: DeploymentLimits | None = None,
    ):
        self._conn = conn
        self._sites_dir = sites_dir
        self._limits = limits or DeploymentLimits()

    def deploy(self, subdomain: str, archive: BinaryIO, owner_id: int) -> SiteRecord:
        with self._site_lock(subdomain):
            self._ensure_can_deploy(subdomain, owner_id)
            self._sites_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=f".{subdomain}-stage-", dir=self._sites_dir
            ) as tmp_dir:
                staging_dir = Path(tmp_dir)
                size_bytes = self._extract_archive(archive, staging_dir)
                return self._publish(subdomain, staging_dir, size_bytes, owner_id)

    def list_for_owner(self, owner_id: int) -> list[SiteRecord]:
        rows = self._conn.execute(
            "SELECT name, created_at, size_bytes, owner_id FROM sites WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
        return [self._record_from_row(r) for r in rows]

    def get_by_name(self, name: str, owner_id: int) -> SiteRecord:
        return self._record_from_row(self._require_access(name, owner_id))

    def list_files(self, name: str, owner_id: int) -> list[FileEntry]:
        self._require_access(name, owner_id)
        site_dir = self._sites_dir / name
        if not site_dir.exists():
            return []

        entries: list[FileEntry] = []
        for item in site_dir.rglob("*"):
            rel = item.relative_to(site_dir)
            entries.append(FileEntry(
                path=str(rel),
                size_bytes=item.stat().st_size if item.is_file() else 0,
                is_dir=item.is_dir(),
                depth=len(rel.parts) - 1,
            ))

        directory_paths = {e.path for e in entries if e.is_dir}

        def sort_key(e: FileEntry) -> tuple:
            parts = Path(e.path).parts
            return tuple(
                (0 if "/".join(parts[:i + 1]) in directory_paths else 1, p.lower())
                for i, p in enumerate(parts)
            )

        entries.sort(key=sort_key)
        return entries

    def delete(self, name: str, owner_id: int) -> None:
        with self._site_lock(name):
            site_dir = self._sites_dir / name
            backup_dir: Path | None = None
            operation_written = False
            transaction_started = False
            try:
                self._begin_write()
                transaction_started = True
                self._require_access(name, owner_id)
                self._ensure_no_active_custom_domain(name)

                if site_dir.exists() or site_dir.is_symlink():
                    backup_dir = self._backup_path(name)
                self._write_operation(
                    name,
                    {
                        "type": "delete",
                        "site": name,
                        "backup": backup_dir.name if backup_dir else None,
                    },
                )
                operation_written = True

                if backup_dir:
                    site_dir.rename(backup_dir)
                    self._sync_directory(self._sites_dir)

                self._conn.execute("DELETE FROM sites WHERE name = ?", (name,))
                self._delete_related_rows(name)
                self._conn.commit()
            except Exception:
                try:
                    if backup_dir and backup_dir.exists():
                        backup_dir.rename(site_dir)
                        self._sync_directory(self._sites_dir)
                finally:
                    if transaction_started and self._conn.in_transaction:
                        self._conn.rollback()
                if operation_written:
                    self._clear_operation(name)
                raise
            else:
                cleanup_succeeded = not backup_dir or self._discard_path(backup_dir)
                if cleanup_succeeded:
                    self._clear_operation(name)

    def reconcile(self) -> None:
        operations_dir = self._sites_dir / _OPERATIONS_DIR
        if not operations_dir.is_dir():
            return

        unresolved_operations: list[Path] = []
        for journal_path in operations_dir.glob("*.json"):
            try:
                operation = json.loads(journal_path.read_text())
                name = operation["site"]
                if journal_path != self._operation_path(name):
                    raise ValueError("operation site does not match journal name")
                with self._site_lock(name):
                    if operation["type"] == "deploy":
                        reconciled = self._reconcile_deploy(operation)
                    elif operation["type"] == "delete":
                        reconciled = self._reconcile_delete(operation)
                    else:
                        raise ValueError("unknown operation type")
                if reconciled:
                    self._clear_operation(name)
                else:
                    unresolved_operations.append(journal_path)
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                unresolved_operations.append(journal_path)
                logger.warning(
                    "Could not reconcile deployment operation %s",
                    journal_path,
                    exc_info=True,
                )

        if unresolved_operations:
            raise RuntimeError(
                f"Could not reconcile {len(unresolved_operations)} deployment operation(s)"
            )

    def _site_row(self, name: str) -> Row | None:
        return self._conn.execute(
            "SELECT name, created_at, size_bytes, owner_id FROM sites WHERE name = ?",
            (name,),
        ).fetchone()

    def _require_access(self, name: str, owner_id: int) -> Row:
        row = self._site_row(name)
        if not row:
            raise NotFound(f"Site '{name}' not found")
        if row["owner_id"] is not None and row["owner_id"] != owner_id:
            raise Forbidden(f"You don't own site '{name}'")
        return row

    @staticmethod
    def _record_from_row(row: Row) -> SiteRecord:
        return SiteRecord(
            name=row["name"],
            owner_id=row["owner_id"],
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
        )

    def _extract_archive(self, archive: BinaryIO, staging_dir: Path) -> int:
        archive_size = self._archive_size(archive)
        if archive_size > self._limits.max_archive_bytes:
            raise PayloadTooLarge(
                f"ZIP exceeds the {self._limits.max_archive_bytes}-byte compressed upload limit"
            )

        try:
            declared_entries = self._declared_entry_count(archive, archive_size)
            self._ensure_entry_limit(declared_entries)
            with zipfile.ZipFile(archive) as zf:
                entries = zf.infolist()
                self._ensure_entry_limit(len(entries))

                files = [entry for entry in entries if not entry.is_dir()]
                declared_size = sum(entry.file_size for entry in files)
                if declared_size > self._limits.max_site_bytes:
                    raise PayloadTooLarge(
                        f"Site exceeds the {self._limits.max_site_bytes}-byte deployed size limit"
                    )

                validated_entries = self._validated_entries(entries, staging_dir)
                size_bytes = self._extract_entries(zf, validated_entries)
                self._sync_tree(staging_dir)
                return size_bytes
        except (struct.error, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise BadRequest("Invalid ZIP file") from exc

    def _extract_entries(
        self,
        archive: zipfile.ZipFile,
        entries: list[tuple[zipfile.ZipInfo, Path]],
    ) -> int:
        total_size = 0

        for entry, target in entries:
            try:
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("xb") as destination:
                    while chunk := source.read(_ARCHIVE_CHUNK_BYTES):
                        total_size += len(chunk)
                        if total_size > self._limits.max_site_bytes:
                            raise PayloadTooLarge(
                                f"Site exceeds the {self._limits.max_site_bytes}-byte deployed size limit"
                            )
                        destination.write(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
            except (FileExistsError, IsADirectoryError, NotADirectoryError) as exc:
                raise BadRequest("ZIP contains conflicting entries") from exc

        return total_size

    def _validated_entries(
        self,
        entries: list[zipfile.ZipInfo],
        staging_dir: Path,
    ) -> list[tuple[zipfile.ZipInfo, Path]]:
        staging_root = staging_dir.resolve()
        explicit_targets: set[Path] = set()
        filesystem_entries: set[Path] = set()
        validated_entries: list[tuple[zipfile.ZipInfo, Path]] = []

        for entry in entries:
            target = self._validated_entry_target(staging_root, entry)
            if target in explicit_targets:
                raise BadRequest("ZIP contains duplicate entries")
            explicit_targets.add(target)

            current = target
            while current != staging_root:
                filesystem_entries.add(current)
                current = current.parent
            self._ensure_entry_limit(len(filesystem_entries))
            validated_entries.append((entry, target))

        return validated_entries

    def _ensure_entry_limit(self, entry_count: int) -> None:
        if entry_count > self._limits.max_entries:
            entry_word = "entry" if self._limits.max_entries == 1 else "entries"
            raise PayloadTooLarge(
                f"Site archive contains more than {self._limits.max_entries} {entry_word}"
            )

    @classmethod
    def _declared_entry_count(cls, archive: BinaryIO, archive_size: int) -> int:
        tail_size = min(archive_size, 22 + 65_535)
        archive.seek(archive_size - tail_size)
        tail = archive.read(tail_size)
        position = len(tail)
        eocd_offset = -1
        eocd = None

        while position > 0:
            candidate = tail.rfind(_EOCD_SIGNATURE, 0, position)
            if candidate < 0:
                break
            if len(tail) - candidate >= 22:
                fields = struct.unpack_from("<4s4H2LH", tail, candidate)
                if candidate + 22 + fields[-1] == len(tail):
                    eocd_offset = archive_size - tail_size + candidate
                    eocd = fields
                    break
            position = candidate

        if eocd is None:
            raise zipfile.BadZipFile("Missing end of central directory")

        _, disk_number, central_disk, disk_entries, total_entries, _, _, _ = eocd
        if total_entries == 0xFFFF:
            # 0xFFFF is either the ZIP64 sentinel or a real count of exactly
            # 65535 entries; only a preceding ZIP64 locator disambiguates.
            zip64_entries = cls._zip64_entry_count(archive, eocd_offset)
            if zip64_entries is not None:
                archive.seek(0)
                return zip64_entries
        if disk_number != 0 or central_disk != 0 or disk_entries != total_entries:
            raise zipfile.BadZipFile("Multi-disk ZIP files are not supported")
        archive.seek(0)
        return total_entries

    @staticmethod
    def _zip64_entry_count(archive: BinaryIO, eocd_offset: int) -> int | None:
        locator_offset = eocd_offset - 20
        if locator_offset < 0:
            return None
        archive.seek(locator_offset)
        locator = archive.read(20)
        if len(locator) != 20:
            return None
        signature, zip64_disk, zip64_offset, disk_count = struct.unpack("<4sLQL", locator)
        if signature != _ZIP64_LOCATOR_SIGNATURE:
            return None
        if zip64_disk != 0 or disk_count != 1:
            raise zipfile.BadZipFile("Invalid ZIP64 locator")

        archive.seek(zip64_offset)
        record = archive.read(56)
        if len(record) != 56:
            raise zipfile.BadZipFile("Incomplete ZIP64 end of central directory")
        fields = struct.unpack("<4sQ2H2L4Q", record)
        if (
            fields[0] != _ZIP64_EOCD_SIGNATURE
            or fields[4] != 0
            or fields[5] != 0
            or fields[6] != fields[7]
        ):
            raise zipfile.BadZipFile("Invalid ZIP64 end of central directory")
        return fields[7]

    @classmethod
    def _sync_tree(cls, root: Path) -> None:
        for directory, _, _ in os.walk(root, topdown=False):
            cls._sync_directory(Path(directory))

    def _validated_entry_target(self, staging_root: Path, entry: zipfile.ZipInfo) -> Path:
        entry_path = PurePosixPath(entry.filename)
        path_bytes = len(entry.filename.encode("utf-8"))
        component_too_long = any(len(part.encode("utf-8")) > 255 for part in entry_path.parts)
        if path_bytes > self._limits.max_path_bytes or component_too_long:
            raise BadRequest("ZIP entry path is too long")
        if ".." in entry_path.parts or "\\" in entry.filename:
            raise BadRequest("ZIP contains path traversal entry")
        if entry.flag_bits & 0x1:
            raise BadRequest("ZIP contains encrypted entry")
        if stat.S_ISLNK(entry.external_attr >> 16):
            raise BadRequest("ZIP contains symbolic link entry")

        target = (staging_root / entry.filename).resolve()
        if not target.is_relative_to(staging_root):
            raise BadRequest("ZIP contains path traversal entry")
        return target

    @staticmethod
    def _archive_size(archive: BinaryIO) -> int:
        try:
            archive.seek(0, 2)
            size = archive.tell()
            archive.seek(0)
            return size
        except (AttributeError, OSError, ValueError) as exc:
            raise BadRequest("ZIP upload is not seekable") from exc

    def _publish(
        self,
        subdomain: str,
        staging_dir: Path,
        size_bytes: int,
        owner_id: int,
    ) -> SiteRecord:
        site_dir = self._sites_dir / subdomain
        backup_dir: Path | None = None
        operation_written = False
        published = False
        transaction_started = False
        now = datetime.now().isoformat()

        try:
            self._begin_write()
            transaction_started = True
            existing = self._site_row(subdomain)
            self._ensure_can_deploy(subdomain, owner_id, existing)
            effective_owner = (
                existing["owner_id"]
                if existing and existing["owner_id"] is not None
                else owner_id
            )

            if existing:
                self._conn.execute(
                    "UPDATE sites SET size_bytes = ?, created_at = ?, owner_id = ? WHERE name = ?",
                    (size_bytes, now, effective_owner, subdomain),
                )
            else:
                self._conn.execute(
                    "INSERT INTO sites (name, size_bytes, created_at, owner_id) VALUES (?, ?, ?, ?)",
                    (subdomain, size_bytes, now, owner_id),
                )

            if site_dir.exists() or site_dir.is_symlink():
                backup_dir = self._backup_path(subdomain)
            self._write_operation(
                subdomain,
                {
                    "type": "deploy",
                    "site": subdomain,
                    "created_at": now,
                    "staging": staging_dir.name,
                    "backup": backup_dir.name if backup_dir else None,
                    "had_site": bool(backup_dir),
                },
            )
            operation_written = True

            if backup_dir:
                site_dir.rename(backup_dir)
            staging_dir.rename(site_dir)
            published = True
            self._sync_directory(self._sites_dir)
            self._conn.commit()
        except Exception:
            try:
                if published and (site_dir.exists() or site_dir.is_symlink()):
                    self._remove_path(site_dir)
                if backup_dir and backup_dir.exists():
                    backup_dir.rename(site_dir)
                self._sync_directory(self._sites_dir)
            finally:
                if transaction_started and self._conn.in_transaction:
                    self._conn.rollback()
            if operation_written:
                self._clear_operation(subdomain)
            raise
        else:
            cleanup_succeeded = not backup_dir or self._discard_path(backup_dir)
            if cleanup_succeeded:
                self._clear_operation(subdomain)

        return SiteRecord(
            name=subdomain,
            owner_id=effective_owner,
            size_bytes=size_bytes,
            created_at=now,
        )

    def _ensure_can_deploy(
        self,
        subdomain: str,
        owner_id: int,
        existing: Row | None = None,
    ) -> None:
        existing = existing if existing is not None else self._site_row(subdomain)
        if existing and existing["owner_id"] is not None and existing["owner_id"] != owner_id:
            raise Forbidden(f"Site '{subdomain}' is owned by another user")

    def _begin_write(self) -> None:
        if self._conn.in_transaction:
            raise RuntimeError("SiteStore writes require a connection without an active transaction")
        self._conn.execute("BEGIN IMMEDIATE")

    def _backup_path(self, name: str) -> Path:
        backup_dir = Path(tempfile.mkdtemp(prefix=f".{name}-backup-", dir=self._sites_dir))
        backup_dir.rmdir()
        return backup_dir

    def _write_operation(self, name: str, operation: dict) -> None:
        operations_dir = self._sites_dir / _OPERATIONS_DIR
        operations_dir.mkdir(parents=True, exist_ok=True)
        journal_path = self._operation_path(name)
        if journal_path.exists():
            raise RuntimeError(f"Site '{name}' has an unresolved deployment operation")
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".operation-",
            dir=operations_dir,
            delete=False,
        ) as journal:
            json.dump(operation, journal)
            journal.flush()
            os.fsync(journal.fileno())
            temporary_path = Path(journal.name)
        os.replace(temporary_path, journal_path)
        self._sync_directory(operations_dir)

    def _clear_operation(self, name: str) -> None:
        operation_path = self._operation_path(name)
        operation_path.unlink(missing_ok=True)
        self._sync_directory(operation_path.parent)

    def _operation_path(self, name: str) -> Path:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        return self._sites_dir / _OPERATIONS_DIR / f"{digest}.json"

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _reconcile_deploy(self, operation: dict) -> bool:
        name = operation["site"]
        site_dir = self._sites_dir / name
        staging_dir = self._operation_child(operation["staging"])
        backup_dir = self._operation_child(operation.get("backup"))
        row = self._site_row(name)
        committed = bool(row and row["created_at"] == operation["created_at"])

        if committed:
            cleanup_succeeded = not backup_dir or self._discard_path(backup_dir)
        else:
            cleanup_succeeded = True
            if backup_dir and backup_dir.exists():
                if site_dir.exists() or site_dir.is_symlink():
                    self._remove_path(site_dir)
                backup_dir.rename(site_dir)
                self._sync_directory(self._sites_dir)
            elif not operation["had_site"] and (site_dir.exists() or site_dir.is_symlink()):
                self._remove_path(site_dir)
                self._sync_directory(self._sites_dir)

        if staging_dir.exists():
            cleanup_succeeded = self._discard_path(staging_dir) and cleanup_succeeded
        return cleanup_succeeded

    def _reconcile_delete(self, operation: dict) -> bool:
        name = operation["site"]
        site_dir = self._sites_dir / name
        backup_dir = self._operation_child(operation.get("backup"))
        committed = self._site_row(name) is None

        if committed:
            return not backup_dir or self._discard_path(backup_dir)
        if backup_dir and backup_dir.exists():
            if site_dir.exists() or site_dir.is_symlink():
                self._remove_path(site_dir)
            backup_dir.rename(site_dir)
            self._sync_directory(self._sites_dir)
        return True

    def _operation_child(self, name: str | None) -> Path | None:
        if name is None:
            return None
        if Path(name).name != name:
            raise ValueError("invalid operation path")
        return self._sites_dir / name

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

    @classmethod
    def _discard_path(cls, path: Path) -> bool:
        try:
            cls._remove_path(path)
            cls._sync_directory(path.parent)
            return True
        except OSError:
            logger.warning("Could not remove stale deployment path %s", path, exc_info=True)
            return False

    @staticmethod
    def _site_lock(name: str) -> threading.Lock:
        return _SITE_LOCKS[hash(name) % len(_SITE_LOCKS)]

    def _delete_related_rows(self, name: str) -> None:
        tables = (
            "deployment_tokens",
            "analytics_daily",
            "analytics_dimensions",
            "analytics_visitors",
        )
        for table in tables:
            try:
                self._conn.execute(f"DELETE FROM {table} WHERE site_name = ?", (name,))
            except OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise

    def _ensure_no_active_custom_domain(self, name: str) -> None:
        self._conn.execute(
            """UPDATE custom_domain_claims SET status = 'expired'
            WHERE status = 'pending' AND expires_at <= ?""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        claim = self._conn.execute(
            """SELECT 1 FROM custom_domain_claims
            WHERE site_name = ? AND status IN ('pending', 'verified') LIMIT 1""",
            (name,),
        ).fetchone()
        if claim:
            raise Conflict("Remove the site's custom domain before deleting the site")
