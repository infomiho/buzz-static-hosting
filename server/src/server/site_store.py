import io
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

from .exceptions import BadRequest, Forbidden, NotFound


@dataclass
class SiteRecord:
    name: str
    owner_id: int
    size_bytes: int
    created_at: str


@dataclass
class FileEntry:
    path: str
    size_bytes: int
    is_dir: bool
    depth: int


class SiteStore:
    def __init__(self, conn: Connection, sites_dir: Path):
        self._conn = conn
        self._sites_dir = sites_dir

    def deploy(self, subdomain: str, zip_content: bytes, owner_id: int) -> SiteRecord:
        existing = self._conn.execute(
            "SELECT owner_id FROM sites WHERE name = ?", (subdomain,)
        ).fetchone()

        if existing and existing["owner_id"] is not None and existing["owner_id"] != owner_id:
            raise Forbidden(f"Site '{subdomain}' is owned by another user")

        site_dir = self._sites_dir / subdomain
        try:
            with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
                for info in zf.infolist():
                    target = (site_dir / info.filename).resolve()
                    if not target.is_relative_to(site_dir.resolve()):
                        raise BadRequest("ZIP contains path traversal entry")
                if site_dir.exists():
                    shutil.rmtree(site_dir)
                site_dir.mkdir(parents=True)
                zf.extractall(site_dir)
        except zipfile.BadZipFile:
            raise BadRequest("Invalid ZIP file")

        size_bytes = sum(f.stat().st_size for f in site_dir.rglob("*") if f.is_file())
        now = datetime.now().isoformat()

        if existing:
            effective_owner = existing["owner_id"] if existing["owner_id"] is not None else owner_id
            self._conn.execute(
                "UPDATE sites SET size_bytes = ?, created_at = ?, owner_id = ? WHERE name = ?",
                (size_bytes, now, effective_owner, subdomain),
            )
        else:
            effective_owner = owner_id
            self._conn.execute(
                "INSERT INTO sites (name, size_bytes, created_at, owner_id) VALUES (?, ?, ?, ?)",
                (subdomain, size_bytes, now, owner_id),
            )

        return SiteRecord(name=subdomain, owner_id=effective_owner, size_bytes=size_bytes, created_at=now)

    def list_for_owner(self, owner_id: int) -> list[SiteRecord]:
        rows = self._conn.execute(
            "SELECT name, created_at, size_bytes, owner_id FROM sites WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
        return [
            SiteRecord(name=r["name"], owner_id=r["owner_id"], size_bytes=r["size_bytes"], created_at=r["created_at"])
            for r in rows
        ]

    def get_by_name(self, name: str, owner_id: int) -> SiteRecord:
        row = self._conn.execute(
            "SELECT name, created_at, size_bytes, owner_id FROM sites WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            raise NotFound(f"Site '{name}' not found")
        if row["owner_id"] is not None and row["owner_id"] != owner_id:
            raise Forbidden(f"You don't own site '{name}'")
        return SiteRecord(
            name=row["name"], owner_id=row["owner_id"],
            size_bytes=row["size_bytes"], created_at=row["created_at"],
        )

    def list_files(self, name: str, owner_id: int) -> list[FileEntry]:
        self.get_by_name(name, owner_id)
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

        def sort_key(e: FileEntry) -> tuple:
            parts = Path(e.path).parts
            return tuple(
                (0 if (site_dir / Path(*parts[:i+1])).is_dir() else 1, p.lower())
                for i, p in enumerate(parts)
            )

        entries.sort(key=sort_key)
        return entries

    def delete(self, name: str, owner_id: int) -> None:
        site = self._conn.execute("SELECT owner_id FROM sites WHERE name = ?", (name,)).fetchone()
        if not site:
            raise NotFound(f"Site '{name}' not found")
        if site["owner_id"] is not None and site["owner_id"] != owner_id:
            raise Forbidden(f"You don't own site '{name}'")

        site_dir = self._sites_dir / name
        if site_dir.exists():
            shutil.rmtree(site_dir)
        self._conn.execute("DELETE FROM sites WHERE name = ?", (name,))
