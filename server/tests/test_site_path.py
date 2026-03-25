import pytest

from server.site_path import InvalidSubdomain, validated_subdomain, resolve_site_file


class TestValidatedSubdomain:
    def test_accepts_simple_names(self):
        assert validated_subdomain("my-site") == "my-site"
        assert validated_subdomain("site_123") == "site_123"
        assert validated_subdomain("simple") == "simple"

    def test_strips_whitespace(self):
        assert validated_subdomain("  my-site  ") == "my-site"

    def test_normalizes_to_lowercase(self):
        assert validated_subdomain("My-Site") == "my-site"

    def test_rejects_empty(self):
        with pytest.raises(InvalidSubdomain):
            validated_subdomain("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(InvalidSubdomain):
            validated_subdomain("   ")

    def test_rejects_path_traversal(self):
        with pytest.raises(InvalidSubdomain):
            validated_subdomain("../escape")

    def test_rejects_slashes(self):
        with pytest.raises(InvalidSubdomain):
            validated_subdomain("foo/bar")

    def test_rejects_dots(self):
        with pytest.raises(InvalidSubdomain):
            validated_subdomain("foo.bar")

    def test_rejects_special_characters(self):
        with pytest.raises(InvalidSubdomain):
            validated_subdomain("my site!")

    def test_rejects_starting_with_hyphen(self):
        with pytest.raises(InvalidSubdomain):
            validated_subdomain("-my-site")

    def test_rejects_too_long(self):
        with pytest.raises(InvalidSubdomain):
            validated_subdomain("a" * 64)

    def test_accepts_max_length(self):
        assert validated_subdomain("a" * 63) == "a" * 63

    def test_accepts_single_char(self):
        assert validated_subdomain("a") == "a"


class TestResolveSiteFile:
    def test_exact_file_match(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "index.html").write_text("<h1>hi</h1>")

        result = resolve_site_file(tmp_path, "my-site", "/index.html")
        assert result == (site / "index.html").resolve()

    def test_clean_url_html_suffix(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "about.html").write_text("<h1>About</h1>")

        result = resolve_site_file(tmp_path, "my-site", "/about")
        assert result == (site / "about.html").resolve()

    def test_clean_url_index_html(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "docs").mkdir()
        (site / "docs" / "index.html").write_text("<h1>Docs</h1>")

        result = resolve_site_file(tmp_path, "my-site", "/docs")
        assert result == (site / "docs" / "index.html").resolve()

    def test_trailing_slash_serves_index(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "index.html").write_text("<h1>Home</h1>")

        result = resolve_site_file(tmp_path, "my-site", "/")
        assert result == (site / "index.html").resolve()

    def test_spa_fallback(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "200.html").write_text("<h1>SPA</h1>")

        result = resolve_site_file(tmp_path, "my-site", "/any/route/here")
        assert result == (site / "200.html").resolve()

    def test_returns_none_for_missing_file(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()

        result = resolve_site_file(tmp_path, "my-site", "/nope.html")
        assert result is None

    def test_returns_none_for_nonexistent_site(self, tmp_path):
        result = resolve_site_file(tmp_path, "ghost", "/index.html")
        assert result is None

    def test_blocks_subdomain_traversal(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("nope")

        with pytest.raises(InvalidSubdomain):
            resolve_site_file(tmp_path, "../secret", "/secret.txt")

    def test_blocks_path_traversal(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "index.html").write_text("hi")
        secret = tmp_path / "secret.txt"
        secret.write_text("nope")

        result = resolve_site_file(tmp_path, "my-site", "/../secret.txt")
        assert result is None

    def test_blocks_encoded_path_traversal(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()

        result = resolve_site_file(tmp_path, "my-site", "/..%2F..%2Fetc/passwd")
        assert result is None

    def test_strips_query_string(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "page.html").write_text("hi")

        result = resolve_site_file(tmp_path, "my-site", "/page.html?v=123")
        assert result == (site / "page.html").resolve()

    def test_nested_files(self, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "assets").mkdir()
        (site / "assets" / "style.css").write_text("body{}")

        result = resolve_site_file(tmp_path, "my-site", "/assets/style.css")
        assert result == (site / "assets" / "style.css").resolve()

    def test_invalid_subdomain_raises(self, tmp_path):
        with pytest.raises(InvalidSubdomain):
            resolve_site_file(tmp_path, "../../etc", "/passwd")
