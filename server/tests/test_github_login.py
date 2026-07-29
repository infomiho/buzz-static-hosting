import asyncio
import io
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx_oauth.exceptions import GetProfileError
from httpx_oauth.oauth2 import GetAccessTokenError

from server.github import GitHubLookupFailed, HttpGitHubClient
from server.github_login import (
    GitHubOAuth,
    GitHubOAuthDenied,
    GitHubOAuthInvalidResponse,
    GitHubOAuthInvalidState,
    GitHubOAuthNotConfigured,
    GitHubOAuthUnavailable,
)
from server.pending_store import PendingStore

REDIRECT_URI = "https://buzz.example/dashboard/login/github/callback"


class FakeOAuthClient:
    def __init__(self, token=None, profile=None):
        self.token = {"access_token": "token"} if token is None else token
        self.profile = {"id": 42, "login": "alice", "name": "Alice"} if profile is None else profile
        self.authorization_calls = []
        self.token_calls = []

    async def get_authorization_url(self, redirect_uri, **kwargs):
        self.authorization_calls.append((redirect_uri, kwargs))
        return "https://github.com/login/oauth/authorize?state=" + kwargs["state"]

    async def get_access_token(self, code, redirect_uri, code_verifier=None):
        self.token_calls.append((code, redirect_uri, code_verifier))
        return self.token

    async def get_profile(self, _token):
        return self.profile


def make_oauth(*, client=None, store=None):
    return GitHubOAuth(
        store or PendingStore(),
        "test-client-id",
        "test-client-secret",
        REDIRECT_URI,
        oauth_client=client or FakeOAuthClient(),
    )


class TestGitHubOAuth:
    def test_start_uses_httpx_oauth_github_authorization_url(self):
        oauth = GitHubOAuth(
            PendingStore(),
            "test-client-id",
            "test-client-secret",
            REDIRECT_URI,
        )

        start = asyncio.run(oauth.start())
        query = parse_qs(urlsplit(start.authorization_url).query)

        assert start.authorization_url.startswith("https://github.com/login/oauth/authorize?")
        assert query["client_id"] == ["test-client-id"]
        assert query["redirect_uri"] == [REDIRECT_URI]
        assert query["scope"] == ["read:user"]
        assert query["state"] == [start.state]
        assert query["code_challenge_method"] == ["S256"]

    def test_start_uses_state_pkce_and_safe_next_path(self):
        client = FakeOAuthClient()
        oauth = make_oauth(client=client)

        start = asyncio.run(oauth.start("/device?from=login"))

        assert parse_qs(urlsplit(start.authorization_url).query)["state"] == [start.state]
        redirect_uri, kwargs = client.authorization_calls[0]
        assert redirect_uri == REDIRECT_URI
        assert kwargs["state"] == start.state
        assert kwargs["code_challenge_method"] == "S256"
        assert kwargs["code_challenge"]
        assert asyncio.run(
            oauth.complete(
                state=start.state,
                browser_nonce=start.browser_nonce,
                code="code",
                error=None,
            )
        ).next_path == "/device?from=login"

    @pytest.mark.parametrize(
        "next_path", ["https://evil.example", "//evil.example", "/%2f%2fevil.example", "//%5B", "#fragment"]
    )
    def test_start_rejects_unsafe_next_path(self, next_path):
        oauth = make_oauth()
        start = asyncio.run(oauth.start(next_path))

        assert asyncio.run(
            oauth.complete(
                state=start.state,
                browser_nonce=start.browser_nonce,
                code="code",
                error=None,
            )
        ).next_path == "/"

    def test_complete_returns_github_user_and_consumes_state(self):
        client = FakeOAuthClient()
        oauth = make_oauth(client=client)
        start = asyncio.run(oauth.start())

        result = asyncio.run(
            oauth.complete(
                state=start.state,
                browser_nonce=start.browser_nonce,
                code="code",
                error=None,
            )
        )

        assert result.user.login == "alice"
        assert client.token_calls[0][0:2] == ("code", REDIRECT_URI)
        assert client.token_calls[0][2]
        with pytest.raises(GitHubOAuthInvalidState):
            asyncio.run(
                oauth.complete(
                    state=start.state,
                    browser_nonce=start.browser_nonce,
                    code="code",
                    error=None,
                )
            )

    def test_complete_rejects_missing_or_mismatched_browser_state(self):
        oauth = make_oauth()
        start = asyncio.run(oauth.start())

        with pytest.raises(GitHubOAuthInvalidState):
            asyncio.run(
                oauth.complete(
                    state=start.state,
                    browser_nonce="other-nonce",
                    code="code",
                    error=None,
                )
            )
        assert asyncio.run(
            oauth.complete(
                state=start.state,
                browser_nonce=start.browser_nonce,
                code="code",
                error=None,
            )
        ).user.login == "alice"

    def test_complete_consumes_denied_authorization(self):
        oauth = make_oauth()
        start = asyncio.run(oauth.start())

        with pytest.raises(GitHubOAuthDenied):
            asyncio.run(
                oauth.complete(
                    state=start.state,
                    browser_nonce=start.browser_nonce,
                    code=None,
                    error="access_denied",
                )
            )
        with pytest.raises(GitHubOAuthInvalidState):
            asyncio.run(
                oauth.complete(
                    state=start.state,
                    browser_nonce=start.browser_nonce,
                    code="code",
                    error=None,
                )
            )

    def test_complete_rejects_missing_token_or_profile_fields(self):
        for token in ({}, []):
            token_oauth = make_oauth(client=FakeOAuthClient(token=token))
            token_start = asyncio.run(token_oauth.start())
            with pytest.raises(GitHubOAuthInvalidResponse):
                asyncio.run(
                    token_oauth.complete(
                        state=token_start.state,
                        browser_nonce=token_start.browser_nonce,
                        code="code",
                        error=None,
                    )
                )

        profile_oauth = make_oauth(client=FakeOAuthClient(profile=[]))
        profile_start = asyncio.run(profile_oauth.start())
        with pytest.raises(GitHubOAuthInvalidResponse):
            asyncio.run(
                profile_oauth.complete(
                    state=profile_start.state,
                    browser_nonce=profile_start.browser_nonce,
                    code="code",
                    error=None,
                )
            )

    def test_complete_normalizes_github_failures(self):
        client = FakeOAuthClient()

        async def fail_token(*_args, **_kwargs):
            raise GetAccessTokenError("offline")

        client.get_access_token = fail_token
        oauth = make_oauth(client=client)
        start = asyncio.run(oauth.start())
        with pytest.raises(GitHubOAuthUnavailable):
            asyncio.run(
                oauth.complete(
                    state=start.state,
                    browser_nonce=start.browser_nonce,
                    code="code",
                    error=None,
                )
            )

        profile_client = FakeOAuthClient()

        async def fail_profile(*_args, **_kwargs):
            raise GetProfileError()

        profile_client.get_profile = fail_profile
        profile_oauth = make_oauth(client=profile_client)
        profile_start = asyncio.run(profile_oauth.start())
        with pytest.raises(GitHubOAuthUnavailable):
            asyncio.run(
                profile_oauth.complete(
                    state=profile_start.state,
                    browser_nonce=profile_start.browser_nonce,
                    code="code",
                    error=None,
                )
            )

    def test_unconfigured_oauth_cannot_start(self):
        oauth = GitHubOAuth(
            PendingStore(), None, None, REDIRECT_URI
        )
        with pytest.raises(GitHubOAuthNotConfigured):
            asyncio.run(oauth.start())


class TestUserLookup:
    def test_transport_failure_is_normalized(self, monkeypatch):
        def fail(*_args, **_kwargs):
            raise URLError("offline")

        monkeypatch.setattr("server.github.urlopen", fail)

        with pytest.raises(GitHubLookupFailed):
            HttpGitHubClient().get_user_by_login("alice")

    def test_invalid_json_is_normalized(self, monkeypatch):
        monkeypatch.setattr(
            "server.github.urlopen", lambda *_args, **_kwargs: io.BytesIO(b"not-json")
        )

        with pytest.raises(GitHubLookupFailed):
            HttpGitHubClient().get_user_by_login("alice")
