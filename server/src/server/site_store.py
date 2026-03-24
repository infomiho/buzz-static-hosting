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
