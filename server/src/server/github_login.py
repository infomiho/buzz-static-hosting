"""GitHub OAuth authorization-code flow for dashboard sign-in."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from httpx_oauth.clients.github import GitHubOAuth2
from httpx_oauth.exceptions import GetProfileError
from httpx_oauth.oauth2 import GetAccessTokenError

from .pending_store import PendingStore
from .site_path import InvalidPath, normalized_url_path

OAUTH_STATE_LIFETIME_SECONDS = 10 * 60


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str
    name: str | None
    avatar_url: str | None = None


@dataclass(frozen=True)
class GitHubOAuthStart:
    authorization_url: str
    state: str
    browser_nonce: str


@dataclass(frozen=True)
class GitHubOAuthCompletion:
    user: GitHubUser
    next_path: str


@dataclass(frozen=True)
class GitHubOAuthAttempt:
    browser_nonce: str
    code_verifier: str
    next_path: str


class GitHubOAuthNotConfigured(Exception):
    pass


class GitHubOAuthInvalidState(Exception):
    pass


class GitHubOAuthDenied(Exception):
    pass


class GitHubOAuthInvalidResponse(Exception):
    pass


class GitHubOAuthUnavailable(Exception):
    pass


class OAuthClient(Protocol):
    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str | None = None,
        scope: list[str] | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
    ) -> str: ...

    async def get_access_token(
        self, code: str, redirect_uri: str, code_verifier: str | None = None
    ) -> dict: ...

    async def get_profile(self, token: str) -> dict: ...


def _safe_next_path(next_path: str | None) -> str:
    if not next_path:
        return "/"
    try:
        parsed = urlsplit(next_path)
    except ValueError:
        return "/"
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return "/"
    try:
        path = normalized_url_path(parsed.path)
    except InvalidPath:
        return "/"
    return path + (f"?{parsed.query}" if parsed.query else "")


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class GitHubOAuth:
    def __init__(
        self,
        store: PendingStore,
        client_id: str | None,
        client_secret: str | None,
        redirect_uri: str,
        oauth_client: OAuthClient | None = None,
    ) -> None:
        self._store = store
        self._redirect_uri = redirect_uri
        self._oauth_client = oauth_client
        if oauth_client is None and client_id and client_secret:
            self._oauth_client = GitHubOAuth2(client_id, client_secret, ["read:user"])

    async def start(
        self, next_path: str | None = None, browser_nonce: str | None = None
    ) -> GitHubOAuthStart:
        if not self._oauth_client:
            raise GitHubOAuthNotConfigured()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        browser_nonce = browser_nonce or secrets.token_urlsafe(32)
        authorization_url = await self._oauth_client.get_authorization_url(
            self._redirect_uri,
            state=state,
            scope=["read:user"],
            code_challenge=_code_challenge(verifier),
            code_challenge_method="S256",
        )
        self._store.put(
            state,
            GitHubOAuthAttempt(
                browser_nonce=browser_nonce,
                code_verifier=verifier,
                next_path=_safe_next_path(next_path),
            ),
            OAUTH_STATE_LIFETIME_SECONDS,
        )
        return GitHubOAuthStart(
            authorization_url=authorization_url,
            state=state,
            browser_nonce=browser_nonce,
        )

    async def complete(
        self,
        *,
        state: str | None,
        browser_nonce: str | None,
        code: str | None,
        error: str | None,
    ) -> GitHubOAuthCompletion:
        if not state or not browser_nonce:
            raise GitHubOAuthInvalidState()
        attempt = self._store.get(state)
        if not isinstance(attempt, GitHubOAuthAttempt) or not hmac.compare_digest(
            browser_nonce, attempt.browser_nonce
        ):
            raise GitHubOAuthInvalidState()
        attempt = self._store.consume(state)
        if not isinstance(attempt, GitHubOAuthAttempt):
            raise GitHubOAuthInvalidState()
        if error:
            raise GitHubOAuthDenied()
        if not code or not self._oauth_client:
            raise GitHubOAuthInvalidResponse()
        try:
            token = await self._oauth_client.get_access_token(
                code, self._redirect_uri, code_verifier=attempt.code_verifier
            )
        except GetAccessTokenError as error:
            raise GitHubOAuthUnavailable() from error
        if not isinstance(token, dict):
            raise GitHubOAuthInvalidResponse()
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GitHubOAuthInvalidResponse()
        try:
            profile = await self._oauth_client.get_profile(access_token)
        except GetProfileError as error:
            raise GitHubOAuthUnavailable() from error
        if not isinstance(profile, dict):
            raise GitHubOAuthInvalidResponse()
        user_id = profile.get("id")
        login = profile.get("login")
        name = profile.get("name")
        avatar_url = profile.get("avatar_url")
        if (
            not isinstance(user_id, int)
            or not isinstance(login, str)
            or name is not None and not isinstance(name, str)
            or avatar_url is not None and not isinstance(avatar_url, str)
        ):
            raise GitHubOAuthInvalidResponse()
        return GitHubOAuthCompletion(
            user=GitHubUser(
                id=user_id,
                login=login,
                name=name,
                avatar_url=avatar_url,
            ),
            next_path=attempt.next_path,
        )
