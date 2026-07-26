from fastapi.testclient import TestClient

def test_every_page_fingerprints_the_stylesheet(make_app, database, tmp_path):
    """A per-router Jinja environment silently drops shared globals, so the
    fingerprint must be present on pages served by every router."""
    with database.connect() as conn:
        conn.execute("INSERT INTO sites (name, owner_id, size_bytes) VALUES ('s', 1, 1)")
    (tmp_path / "s").mkdir(exist_ok=True)
    client = TestClient(make_app(dev_mode=True))
    for path in ("/", "/dashboard/sites/s", "/account/"):
        body = client.get(path).text
        assert "style.css?v=" in body, path
        assert "style.css?v=\"" not in body, f"{path} rendered an empty fingerprint"
