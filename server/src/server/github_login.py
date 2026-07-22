"""GitHub's OAuth device authorization flow for browser sign-in.

This owns the pending device-code state and GitHub's error taxonomy and depends
only on the GitHub transport client. It resolves an external GitHub identity and
never touches Buzz users, sessions, or the allowlist: the caller maps the
returned GitHubUser to a Buzz account (AuthService.login_with_github).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .github import GitHubClient

DEFAULT_EXPIRES_IN = 900
DEFAULT_INTERVAL = 5


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str
    name: str | None


class GitHubDeviceFlowPending(Exception):
    pass


class GitHubDeviceFlowSlowDown(Exception):
    def __init__(self, interval: int):
        self.interval = interval


class GitHubDeviceFlowExpired(Exception):
    pass


class GitHubDeviceFlowDenied(Exception):
    pass


class GitHubDeviceFlowFailed(Exception):
    def __init__(self, detail: str):
        self.detail = detail


class GitHubDeviceFlow:
    def __init__(self, github: GitHubClient | None, client_id: str | None) -> None:
        self._github = github
        self._client_id = client_id
        self._device_codes: dict[str, dict] = {}

    def start(self) -> dict:
        if not self._github or not self._client_id:
            raise GitHubDeviceFlowFailed("GitHub OAuth not configured")

        result = self._github.start_device_flow(self._client_id)
        if "device_code" not in result:
            raise GitHubDeviceFlowFailed("Failed to start device flow")

        self._device_codes[result["device_code"]] = {
            "expires_at": datetime.now()
            + timedelta(seconds=result.get("expires_in", DEFAULT_EXPIRES_IN)),
        }
        return {
            "device_code": result["device_code"],
            "user_code": result["user_code"],
            "verification_uri": result.get("verification_uri", "https://github.com/login/device"),
            "interval": result.get("interval", DEFAULT_INTERVAL),
            "expires_in": result.get("expires_in", DEFAULT_EXPIRES_IN),
        }

    def poll(self, device_code: str) -> GitHubUser:
        if not self._github or not self._client_id:
            raise GitHubDeviceFlowFailed("GitHub OAuth not configured")

        pending = self._device_codes.get(device_code)
        if not pending:
            raise GitHubDeviceFlowExpired()
        if datetime.now() > pending["expires_at"]:
            del self._device_codes[device_code]
            raise GitHubDeviceFlowExpired()

        result = self._github.poll_device_flow(self._client_id, device_code)
        if "error" in result:
            error = result["error"]
            if error == "authorization_pending":
                raise GitHubDeviceFlowPending()
            if error == "slow_down":
                raise GitHubDeviceFlowSlowDown(result.get("interval", 10))
            del self._device_codes[device_code]
            if error == "expired_token":
                raise GitHubDeviceFlowExpired()
            if error == "access_denied":
                raise GitHubDeviceFlowDenied()
            raise GitHubDeviceFlowFailed(result.get("error_description", error))

        github_user = self._github.get_user(result["access_token"])
        # GitHub has granted the token, so the device code is consumed even when
        # the user turns out not to be allowed downstream.
        del self._device_codes[device_code]
        return GitHubUser(
            id=github_user["id"],
            login=github_user["login"],
            name=github_user.get("name"),
        )
