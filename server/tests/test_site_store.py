import io
import sqlite3
import zipfile

import pytest

from server.exceptions import BadRequest, Forbidden, NotFound
from server.site_store import SiteStore, SiteRecord, FileEntry


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


class TestDeploy:
    def test_creates_files_and_db_row(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        zip_content = make_zip({"index.html": "<h1>hello</h1>"})

        record = store.deploy("my-site", zip_content, owner_id=1)

        assert record.name == "my-site"
        assert record.owner_id == 1
        assert record.size_bytes > 0
        assert (tmp_path / "my-site" / "index.html").read_text() == "<h1>hello</h1>"

        row = conn.execute("SELECT * FROM sites WHERE name = ?", ("my-site",)).fetchone()
        assert row["owner_id"] == 1
        assert row["size_bytes"] == record.size_bytes

    def test_bad_zip_raises_bad_request(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        with pytest.raises(BadRequest, match="Invalid ZIP file"):
            store.deploy("my-site", b"not a zip", owner_id=1)

    def test_other_users_site_raises_forbidden(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        zip_content = make_zip({"index.html": "v1"})

        store.deploy("taken-site", zip_content, owner_id=1)

        with pytest.raises(Forbidden, match="owned by another user"):
            store.deploy("taken-site", zip_content, owner_id=2)

    def test_redeploy_own_site_updates_row(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        first = store.deploy("my-site", make_zip({"a.txt": "v1"}), owner_id=1)
        second = store.deploy("my-site", make_zip({"a.txt": "v2", "b.txt": "new"}), owner_id=1)

        assert second.name == first.name
        assert second.owner_id == 1
        assert second.size_bytes != first.size_bytes
        assert (tmp_path / "my-site" / "b.txt").read_text() == "new"

        rows = conn.execute("SELECT * FROM sites WHERE name = 'my-site'").fetchall()
        assert len(rows) == 1

    def test_redeploy_removes_stale_files(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        store.deploy("my-site", make_zip({"old.html": "v1", "keep.html": "v1"}), owner_id=1)
        assert (tmp_path / "my-site" / "old.html").exists()

        store.deploy("my-site", make_zip({"keep.html": "v2"}), owner_id=1)
        assert not (tmp_path / "my-site" / "old.html").exists()
        assert (tmp_path / "my-site" / "keep.html").read_text() == "v2"

    def test_zip_path_traversal_raises_bad_request(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../escape.txt", "gotcha")
        zip_content = buf.getvalue()

        with pytest.raises(BadRequest, match="path traversal"):
            store.deploy("my-site", zip_content, owner_id=1)

    def test_unclaimed_site_gets_adopted(self, tmp_path):
        conn = make_db()
        conn.execute(
            "INSERT INTO sites (name, size_bytes, owner_id) VALUES (?, ?, ?)",
            ("orphan", 0, None),
        )
        store = SiteStore(conn, tmp_path)
        zip_content = make_zip({"index.html": "claimed"})

        record = store.deploy("orphan", zip_content, owner_id=5)

        assert record.owner_id == 5
        row = conn.execute("SELECT owner_id FROM sites WHERE name = 'orphan'").fetchone()
        assert row["owner_id"] == 5


class TestListForOwner:
    def test_returns_only_owned_sites(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("site-a", make_zip({"a.txt": "a"}), owner_id=1)
        store.deploy("site-b", make_zip({"b.txt": "b"}), owner_id=1)
        store.deploy("site-c", make_zip({"c.txt": "c"}), owner_id=2)

        sites = store.list_for_owner(owner_id=1)

        names = [s.name for s in sites]
        assert "site-a" in names
        assert "site-b" in names
        assert "site-c" not in names

    def test_returns_empty_for_no_sites(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)

        assert store.list_for_owner(owner_id=99) == []


class TestDelete:
    def test_removes_directory_and_db_row(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("doomed", make_zip({"index.html": "bye"}), owner_id=1)

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
        store.deploy("owned", make_zip({"index.html": "mine"}), owner_id=1)

        with pytest.raises(Forbidden, match="don't own"):
            store.delete("owned", owner_id=2)


class TestListFiles:
    def test_returns_files_with_correct_paths_and_sizes(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", make_zip({"index.html": "<h1>hi</h1>", "style.css": "body{}"}), owner_id=1)

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
        store.deploy("my-site", make_zip({"assets/logo.png": "img", "index.html": "hi"}), owner_id=1)

        files = store.list_files("my-site", owner_id=1)

        dirs = [f for f in files if f.is_dir]
        assert len(dirs) == 1
        assert dirs[0].path == "assets"
        assert dirs[0].size_bytes == 0
        assert dirs[0].depth == 0

    def test_nested_directories_with_correct_depth(self, tmp_path):
        conn = make_db()
        store = SiteStore(conn, tmp_path)
        store.deploy("my-site", make_zip({
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
        store.deploy("my-site", make_zip({
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
        store.deploy("secret", make_zip({"index.html": "hi"}), owner_id=1)

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
