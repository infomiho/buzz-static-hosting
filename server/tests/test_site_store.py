import io
import sqlite3
import struct
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server.exceptions import BadRequest, Forbidden, NotFound, PayloadTooLarge
from server.site_store import DeploymentLimits, SiteStore


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sites ("
        "  name TEXT PRIMARY KEY,"
        "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  size_bytes INTEGER,"
        "  owner_id INTEGER"
        ")"
    )
    return conn


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def archive(files: dict[str, str]) -> io.BytesIO:
    return io.BytesIO(make_zip(files))


class FailingCommitConnection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @property
    def in_transaction(self):
        return self._conn.in_transaction

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def commit(self):
        raise sqlite3.OperationalError("commit failed")

    def rollback(self):
        return self._conn.rollback()


class TestDeploy:
    def test_creates_files_and_db_row(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        zip_content = archive({"index.html": "<h1>hello</h1>"})

        record = store.deploy("my-site", zip_content, owner_id=1)

        assert record.name == "my-site"
        assert record.owner_id == 1
        assert record.size_bytes > 0
        assert (tmp_path / "my-site" / "index.html").read_text() == "<h1>hello</h1>"
        assert list((tmp_path / ".operations").glob("*.json")) == []

        row = conn.execute("SELECT * FROM sites WHERE name = ?", ("my-site",)).fetchone()
        assert row["owner_id"] == 1
        assert row["size_bytes"] == record.size_bytes

    def test_bad_zip_raises_bad_request(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        with pytest.raises(BadRequest, match="Invalid ZIP file"):
            store.deploy("my-site", io.BytesIO(b"not a zip"), owner_id=1)

    def test_other_users_site_raises_forbidden(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        zip_content = archive({"index.html": "v1"})

        store.deploy("taken-site", zip_content, owner_id=1)

        with pytest.raises(Forbidden, match="owned by another user"):
            store.deploy("taken-site", zip_content, owner_id=2)

    def test_redeploy_own_site_updates_row(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        first = store.deploy("my-site", archive({"a.txt": "v1"}), owner_id=1)
        second = store.deploy("my-site", archive({"a.txt": "v2", "b.txt": "new"}), owner_id=1)

        assert second.name == first.name
        assert second.owner_id == 1
        assert second.size_bytes != first.size_bytes
        assert (tmp_path / "my-site" / "b.txt").read_text() == "new"

        rows = conn.execute("SELECT * FROM sites WHERE name = 'my-site'").fetchall()
        assert len(rows) == 1

    def test_redeploy_removes_stale_files(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        store.deploy("my-site", archive({"old.html": "v1", "keep.html": "v1"}), owner_id=1)
        assert (tmp_path / "my-site" / "old.html").exists()

        store.deploy("my-site", archive({"keep.html": "v2"}), owner_id=1)
        assert not (tmp_path / "my-site" / "old.html").exists()
        assert (tmp_path / "my-site" / "keep.html").read_text() == "v2"

    def test_zip_path_traversal_raises_bad_request(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../escape.txt", "gotcha")
        zip_content = io.BytesIO(buf.getvalue())

        with pytest.raises(BadRequest, match="path traversal"):
            store.deploy("my-site", zip_content, owner_id=1)

    def test_unclaimed_site_gets_adopted(self, tmp_path):
        conn = make_db()
        conn.execute(
            "INSERT INTO sites (name, size_bytes, owner_id) VALUES (?, ?, ?)",
            ("orphan", 0, None),
        )
        conn.commit()
        store = SiteStore(conn, tmp_path)
        zip_content = archive({"index.html": "claimed"})

        record = store.deploy("orphan", zip_content, owner_id=5)

        assert record.owner_id == 5
        row = conn.execute("SELECT owner_id FROM sites WHERE name = 'orphan'").fetchone()
        assert row["owner_id"] == 5

    def test_rejects_compressed_archive_over_limit(self, tmp_path):
        conn = make_db()
        zip_content = archive({"index.html": "hello"})
        limits = DeploymentLimits(max_archive_bytes=len(zip_content.getvalue()) - 1)

        with pytest.raises(PayloadTooLarge, match="compressed upload limit"):
            SiteStore(conn, tmp_path, limits).deploy("my-site", zip_content, owner_id=1)

        assert not (tmp_path / "my-site").exists()
        assert conn.execute("SELECT * FROM sites").fetchall() == []

    def test_rejects_expanded_site_over_limit_without_replacing_current_site(self, tmp_path):
        conn = make_db()
        SiteStore(conn, tmp_path).deploy("my-site", archive({"index.html": "old"}), owner_id=1)
        original_row = conn.execute("SELECT * FROM sites WHERE name = 'my-site'").fetchone()
        limits = DeploymentLimits(max_site_bytes=3)

        with pytest.raises(PayloadTooLarge, match="deployed size limit"):
            SiteStore(conn, tmp_path, limits).deploy(
                "my-site", archive({"index.html": "replacement"}), owner_id=1
            )

        assert (tmp_path / "my-site" / "index.html").read_text() == "old"
        row = conn.execute("SELECT * FROM sites WHERE name = 'my-site'").fetchone()
        assert dict(row) == dict(original_row)
        assert list((tmp_path / ".operations").glob("*.json")) == []

    def test_rejects_too_many_files(self, tmp_path):
        conn = make_db()
        limits = DeploymentLimits(max_entries=1)

        with pytest.raises(PayloadTooLarge, match="more than 1 entry"):
            SiteStore(conn, tmp_path, limits).deploy(
                "my-site", archive({"one.txt": "1", "two.txt": "2"}), owner_id=1
            )

    def test_directory_entries_count_toward_archive_limit(self, tmp_path):
        conn = make_db()
        limits = DeploymentLimits(max_entries=1)
        zip_content = io.BytesIO()
        with zipfile.ZipFile(zip_content, "w") as zf:
            zf.mkdir("one/")
            zf.mkdir("two/")
        zip_content.seek(0)

        with pytest.raises(PayloadTooLarge, match="more than 1 entry"):
            SiteStore(conn, tmp_path, limits).deploy("my-site", zip_content, owner_id=1)

    def test_implicit_directories_count_toward_archive_limit(self, tmp_path):
        conn = make_db()
        limits = DeploymentLimits(max_entries=3)

        with pytest.raises(PayloadTooLarge, match="more than 3 entries"):
            SiteStore(conn, tmp_path, limits).deploy(
                "my-site", archive({"one/two/three/index.html": "content"}), owner_id=1
            )

    def test_conflicting_file_and_directory_entries_are_bad_request(self, tmp_path):
        conn = make_db()
        zip_content = io.BytesIO()
        with zipfile.ZipFile(zip_content, "w") as zf:
            zf.writestr("assets", "file")
            zf.mkdir("assets/")
        zip_content.seek(0)

        with pytest.raises(BadRequest, match="duplicate entries"):
            SiteStore(conn, tmp_path).deploy("my-site", zip_content, owner_id=1)

    def test_rejects_overlong_archive_path(self, tmp_path):
        conn = make_db()
        limits = DeploymentLimits(max_path_bytes=10)

        with pytest.raises(BadRequest, match="path is too long"):
            SiteStore(conn, tmp_path, limits).deploy(
                "my-site", archive({"long-file-name.txt": "content"}), owner_id=1
            )

    def test_publish_failure_restores_previous_site_and_metadata(self, tmp_path, monkeypatch):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({"index.html": "old"}), owner_id=1)
        original_row = conn.execute("SELECT * FROM sites WHERE name = 'my-site'").fetchone()
        original_rename = Path.rename

        def fail_staging_publish(path, target):
            if "stage" in path.name and Path(target) == tmp_path / "my-site":
                raise OSError("publish failed")
            return original_rename(path, target)

        monkeypatch.setattr(Path, "rename", fail_staging_publish)

        with pytest.raises(OSError, match="publish failed"):
            store.deploy("my-site", archive({"index.html": "new"}), owner_id=1)

        assert (tmp_path / "my-site" / "index.html").read_text() == "old"
        row = conn.execute("SELECT * FROM sites WHERE name = 'my-site'").fetchone()
        assert dict(row) == dict(original_row)

    def test_commit_failure_restores_previous_site_and_metadata(self, tmp_path):
        conn = make_db()
        SiteStore(conn, tmp_path).deploy("my-site", archive({"index.html": "old"}), owner_id=1)
        original_row = conn.execute("SELECT * FROM sites WHERE name = 'my-site'").fetchone()

        with pytest.raises(sqlite3.OperationalError, match="commit failed"):
            SiteStore(FailingCommitConnection(conn), tmp_path).deploy(
                "my-site", archive({"index.html": "new"}), owner_id=1
            )

        assert (tmp_path / "my-site" / "index.html").read_text() == "old"
        row = conn.execute("SELECT * FROM sites WHERE name = 'my-site'").fetchone()
        assert dict(row) == dict(original_row)

    def test_concurrent_first_deploy_has_one_owner_and_matching_files(self, tmp_path):
        db_path = tmp_path / "sites.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE sites ("
            "name TEXT PRIMARY KEY, created_at DATETIME, size_bytes INTEGER, owner_id INTEGER)"
        )
        conn.commit()
        conn.close()

        def deploy_as(owner_id: int):
            thread_conn = sqlite3.connect(db_path)
            thread_conn.row_factory = sqlite3.Row
            try:
                SiteStore(thread_conn, tmp_path / "content").deploy(
                    "shared", archive({"index.html": str(owner_id)}), owner_id
                )
                return owner_id, "deployed"
            except Forbidden:
                return owner_id, "forbidden"
            finally:
                thread_conn.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(deploy_as, (1, 2)))

        winner = next(owner_id for owner_id, result in results if result == "deployed")
        assert sorted(result for _, result in results) == ["deployed", "forbidden"]
        assert (tmp_path / "content" / "shared" / "index.html").read_text() == str(winner)
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("SELECT owner_id FROM sites WHERE name = 'shared'").fetchone()[0] == winner
        finally:
            conn.close()


class TestListForOwner:
    def test_returns_only_owned_sites(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("site-a", archive({"a.txt": "a"}), owner_id=1)
        store.deploy("site-b", archive({"b.txt": "b"}), owner_id=1)
        store.deploy("site-c", archive({"c.txt": "c"}), owner_id=2)

        sites = store.list_for_owner(owner_id=1)

        names = [s.name for s in sites]
        assert "site-a" in names
        assert "site-b" in names
        assert "site-c" not in names

    def test_returns_empty_for_no_sites(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        assert store.list_for_owner(owner_id=99) == []


class TestDeclaredEntryCount:
    def test_accepts_plain_archive_with_exactly_65535_entries(self):
        eocd = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0, 0, 0)
        data = b"\x00" * 40 + eocd

        count = SiteStore._declared_entry_count(io.BytesIO(data), len(data))

        assert count == 0xFFFF

    def test_reads_entry_count_from_zip64_record(self):
        zip64_eocd = struct.pack(
            "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, 70_000, 70_000, 0, 0
        )
        locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
        eocd = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0, 0, 0)
        data = zip64_eocd + locator + eocd

        count = SiteStore._declared_entry_count(io.BytesIO(data), len(data))

        assert count == 70_000


class TestDelete:
    def test_removes_directory_and_db_row(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("doomed", archive({"index.html": "bye"}), owner_id=1)

        store.delete("doomed", owner_id=1)

        assert not (tmp_path / "doomed").exists()
        assert conn.execute("SELECT * FROM sites WHERE name = 'doomed'").fetchone() is None

    def test_missing_site_raises_not_found(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        with pytest.raises(NotFound, match="not found"):
            store.delete("nonexistent", owner_id=1)

    def test_wrong_owner_raises_forbidden(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("owned", archive({"index.html": "mine"}), owner_id=1)

        with pytest.raises(Forbidden, match="don't own"):
            store.delete("owned", owner_id=2)

    def test_purges_deployment_tokens_for_site(self, tmp_path):
        conn = make_db()
        conn.execute(
            "CREATE TABLE deployment_tokens ("
            "  id TEXT PRIMARY KEY,"
            "  name TEXT,"
            "  site_name TEXT,"
            "  user_id INTEGER"
            ")"
        )
        store = SiteStore(conn, tmp_path)
        store.deploy("doomed", archive({"index.html": "bye"}), owner_id=1)
        conn.execute(
            "INSERT INTO deployment_tokens (id, name, site_name, user_id) "
            "VALUES ('doomed-token', 'ci', 'doomed', 1)"
        )
        conn.execute(
            "INSERT INTO deployment_tokens (id, name, site_name, user_id) "
            "VALUES ('other-token', 'ci', 'other-site', 1)"
        )
        conn.commit()

        store.delete("doomed", owner_id=1)

        remaining = conn.execute("SELECT id FROM deployment_tokens").fetchall()
        assert [row["id"] for row in remaining] == ["other-token"]

    def test_commit_failure_restores_deleted_site(self, tmp_path):
        conn = make_db()
        SiteStore(conn, tmp_path).deploy("doomed", archive({"index.html": "old"}), owner_id=1)

        with pytest.raises(sqlite3.OperationalError, match="commit failed"):
            SiteStore(FailingCommitConnection(conn), tmp_path).delete("doomed", owner_id=1)

        assert (tmp_path / "doomed" / "index.html").read_text() == "old"
        assert conn.execute("SELECT * FROM sites WHERE name = 'doomed'").fetchone()


class TestReconcile:
    def test_restores_previous_site_when_deploy_did_not_commit(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({"index.html": "old"}), owner_id=1)
        site_dir = tmp_path / "my-site"
        backup_dir = tmp_path / ".my-site-backup-crash"
        site_dir.rename(backup_dir)
        site_dir.mkdir()
        (site_dir / "index.html").write_text("new")
        store._write_operation(
            "my-site",
            {
                "type": "deploy",
                "site": "my-site",
                "created_at": "uncommitted",
                "staging": ".my-site-stage-crash",
                "backup": backup_dir.name,
                "had_site": True,
            },
        )

        store.reconcile()

        assert (site_dir / "index.html").read_text() == "old"
        assert not backup_dir.exists()
        assert list((tmp_path / ".operations").glob("*.json")) == []

    def test_keeps_published_site_when_deploy_committed(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({"index.html": "old"}), owner_id=1)
        site_dir = tmp_path / "my-site"
        backup_dir = tmp_path / ".my-site-backup-crash"
        site_dir.rename(backup_dir)
        site_dir.mkdir()
        (site_dir / "index.html").write_text("new")
        conn.execute("UPDATE sites SET created_at = 'committed' WHERE name = 'my-site'")
        conn.commit()
        store._write_operation(
            "my-site",
            {
                "type": "deploy",
                "site": "my-site",
                "created_at": "committed",
                "staging": ".my-site-stage-crash",
                "backup": backup_dir.name,
                "had_site": True,
            },
        )

        store.reconcile()

        assert (site_dir / "index.html").read_text() == "new"
        assert not backup_dir.exists()
        assert list((tmp_path / ".operations").glob("*.json")) == []

    def test_restores_site_when_delete_did_not_commit(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({"index.html": "old"}), owner_id=1)
        site_dir = tmp_path / "my-site"
        backup_dir = tmp_path / ".my-site-backup-crash"
        site_dir.rename(backup_dir)
        store._write_operation(
            "my-site",
            {
                "type": "delete",
                "site": "my-site",
                "backup": backup_dir.name,
            },
        )

        store.reconcile()

        assert (site_dir / "index.html").read_text() == "old"
        assert not backup_dir.exists()
        assert list((tmp_path / ".operations").glob("*.json")) == []

    def test_unreadable_operation_blocks_startup(self, tmp_path):
        conn = make_db()
        operations_dir = tmp_path / ".operations"
        operations_dir.mkdir()
        (operations_dir / "broken.json").write_text("not json")

        with pytest.raises(RuntimeError, match="Could not reconcile 1 deployment operation"):
            SiteStore(conn, tmp_path).reconcile()

    def test_unresolved_operation_blocks_next_deploy(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({"index.html": "old"}), owner_id=1)
        store._write_operation(
            "my-site",
            {
                "type": "deploy",
                "site": "my-site",
                "created_at": "unresolved",
                "staging": ".my-site-stage-unresolved",
                "backup": None,
                "had_site": True,
            },
        )

        with pytest.raises(RuntimeError, match="unresolved deployment operation"):
            store.deploy("my-site", archive({"index.html": "new"}), owner_id=1)

        assert (tmp_path / "my-site" / "index.html").read_text() == "old"


class TestListFiles:
    def test_returns_files_with_correct_paths_and_sizes(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({"index.html": "<h1>hi</h1>", "style.css": "body{}"}), owner_id=1)

        files = store.list_files("my-site", owner_id=1)

        paths = [f.path for f in files]
        assert "index.html" in paths
        assert "style.css" in paths
        for f in files:
            assert not f.is_dir
            assert f.size_bytes > 0
            assert f.depth == 0

    def test_returns_directories_as_entries(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({"assets/logo.png": "img", "index.html": "hi"}), owner_id=1)

        files = store.list_files("my-site", owner_id=1)

        dirs = [f for f in files if f.is_dir]
        assert len(dirs) == 1
        assert dirs[0].path == "assets"
        assert dirs[0].size_bytes == 0
        assert dirs[0].depth == 0

    def test_nested_directories_with_correct_depth(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({
            "index.html": "hi",
            "assets/css/style.css": "body{}",
            "assets/img/logo.png": "img",
        }), owner_id=1)

        files = store.list_files("my-site", owner_id=1)

        by_path = {f.path: f for f in files}
        assert by_path["assets"].is_dir
        assert by_path["assets"].depth == 0
        assert by_path["assets/css"].is_dir
        assert by_path["assets/css"].depth == 1
        assert by_path["assets/css/style.css"].depth == 2
        assert not by_path["assets/css/style.css"].is_dir

    def test_sorts_directories_before_files(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", archive({
            "zebra.txt": "z",
            "assets/logo.png": "img",
            "about.html": "a",
        }), owner_id=1)

        files = store.list_files("my-site", owner_id=1)

        top_level = [f for f in files if f.depth == 0]
        assert top_level[0].path == "assets"
        assert top_level[0].is_dir
        assert not top_level[1].is_dir

    def test_nonexistent_site_raises_not_found(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        with pytest.raises(NotFound, match="not found"):
            store.list_files("ghost", owner_id=1)

    def test_wrong_owner_raises_forbidden(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("secret", archive({"index.html": "hi"}), owner_id=1)

        with pytest.raises(Forbidden, match="don't own"):
            store.list_files("secret", owner_id=2)

    def test_empty_site_returns_empty_list(self, tmp_path):
        conn = make_db()
        conn.execute(
            "INSERT INTO sites (name, size_bytes, owner_id) VALUES (?, ?, ?)",
            ("empty", 0, 1),
        )
        store = SiteStore(conn, tmp_path)

        assert store.list_files("empty", owner_id=1) == []
