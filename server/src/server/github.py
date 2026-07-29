from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class GitHubClient(Protocol):
    def get_user_by_login(self, login: str) -> dict[str, Any]: ...


class GitHubUserNotFound(Exception):
    pass


class GitHubLookupFailed(Exception):
    pass


class HttpGitHubClient:
    def get_user_by_login(self, login: str) -> dict[str, Any]:
        req = Request(f"https://api.github.com/users/{quote(login, safe='')}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "Buzz-Static-Hosting")
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as error:
            if error.code == 404:
                raise GitHubUserNotFound(login) from error
            raise GitHubLookupFailed() from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise GitHubLookupFailed() from error

class FakeGitHubClient:
    def __init__(self) -> None:
        self.user: dict[str, Any] = {"id": 42, "login": "alice", "name": "Alice"}

    def get_user_by_login(self, login: str) -> dict[str, Any]:
        if login.lower() != self.user["login"].lower():
            raise GitHubUserNotFound(login)
        return self.user
