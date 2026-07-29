from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class GitHubClient(Protocol):
    def start_device_flow(self, client_id: str) -> dict[str, Any]: ...
    def poll_device_flow(self, client_id: str, device_code: str) -> dict[str, Any]: ...
    def get_user(self, access_token: str) -> dict[str, Any]: ...
    def get_user_by_login(self, login: str) -> dict[str, Any]: ...


class GitHubUserNotFound(Exception):
    pass


class GitHubLookupFailed(Exception):
    pass


class HttpGitHubClient:
    def start_device_flow(self, client_id: str) -> dict[str, Any]:
        return self._post(
            "https://github.com/login/device/code",
            {"client_id": client_id, "scope": "read:user"},
        )

    def poll_device_flow(self, client_id: str, device_code: str) -> dict[str, Any]:
        return self._post(
            "https://github.com/login/oauth/access_token",
            {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )

    def get_user(self, access_token: str) -> dict[str, Any]:
        req = Request("https://api.github.com/user")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "Buzz-Static-Hosting")
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())

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

    def _post(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        req = Request(
            url,
            data=urlencode(data).encode(),
            headers={"Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            return json.loads(e.read().decode())


class FakeGitHubClient:
    def __init__(self) -> None:
        self.user: dict[str, Any] = {"id": 42, "login": "alice", "name": "Alice"}
        self.device_code_response: dict[str, Any] = {
            "device_code": "dc_test",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "interval": 5,
            "expires_in": 900,
        }
        self.poll_response: dict[str, Any] = {"access_token": "fake_token"}

    def start_device_flow(self, client_id: str) -> dict[str, Any]:
        return self.device_code_response

    def poll_device_flow(self, client_id: str, device_code: str) -> dict[str, Any]:
        return self.poll_response

    def get_user(self, access_token: str) -> dict[str, Any]:
        return self.user

    def get_user_by_login(self, login: str) -> dict[str, Any]:
        if login.lower() != self.user["login"].lower():
            raise GitHubUserNotFound(login)
        return self.user
