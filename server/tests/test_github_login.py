import pytest

from server.github import FakeGitHubClient
from server.github_login import (
    GitHubDeviceFlow,
    GitHubDeviceFlowDenied,
    GitHubDeviceFlowExpired,
    GitHubDeviceFlowFailed,
    GitHubDeviceFlowPending,
    GitHubDeviceFlowSlowDown,
    GitHubUser,
)


def make_flow(**github_overrides):
    github = FakeGitHubClient()
    for key, value in github_overrides.items():
        setattr(github, key, value)
    return GitHubDeviceFlow(github, "test-client-id")


class TestStart:
    def test_returns_device_and_user_code(self):
        start = make_flow().start()
        assert "device_code" in start
        assert "user_code" in start
        assert start["verification_uri"]

    def test_unconfigured_raises(self):
        with pytest.raises(GitHubDeviceFlowFailed):
            GitHubDeviceFlow(None, None).start()

    def test_missing_device_code_raises(self):
        flow = make_flow(device_code_response={"user_code": "ABCD"})
        with pytest.raises(GitHubDeviceFlowFailed):
            flow.start()


class TestPoll:
    def test_returns_github_user(self):
        flow = make_flow()
        start = flow.start()
        assert flow.poll(start["device_code"]) == GitHubUser(id=42, login="alice", name="Alice")

    def test_pending(self):
        flow = make_flow(poll_response={"error": "authorization_pending"})
        start = flow.start()
        with pytest.raises(GitHubDeviceFlowPending):
            flow.poll(start["device_code"])

    def test_slow_down_carries_interval(self):
        flow = make_flow(poll_response={"error": "slow_down", "interval": 10})
        start = flow.start()
        with pytest.raises(GitHubDeviceFlowSlowDown) as exc_info:
            flow.poll(start["device_code"])
        assert exc_info.value.interval == 10

    def test_expired(self):
        flow = make_flow(poll_response={"error": "expired_token"})
        start = flow.start()
        with pytest.raises(GitHubDeviceFlowExpired):
            flow.poll(start["device_code"])

    def test_denied(self):
        flow = make_flow(poll_response={"error": "access_denied"})
        start = flow.start()
        with pytest.raises(GitHubDeviceFlowDenied):
            flow.poll(start["device_code"])

    def test_unknown_error_carries_description(self):
        flow = make_flow(poll_response={"error": "boom", "error_description": "kaboom"})
        start = flow.start()
        with pytest.raises(GitHubDeviceFlowFailed) as exc_info:
            flow.poll(start["device_code"])
        assert exc_info.value.detail == "kaboom"

    def test_unknown_device_code_reads_as_expired(self):
        with pytest.raises(GitHubDeviceFlowExpired):
            make_flow().poll("nonexistent")

    def test_success_consumes_the_device_code(self):
        flow = make_flow()
        start = flow.start()
        flow.poll(start["device_code"])
        with pytest.raises(GitHubDeviceFlowExpired):
            flow.poll(start["device_code"])

    def test_pending_does_not_consume_the_device_code(self):
        flow = make_flow(poll_response={"error": "authorization_pending"})
        start = flow.start()
        with pytest.raises(GitHubDeviceFlowPending):
            flow.poll(start["device_code"])
        with pytest.raises(GitHubDeviceFlowPending):
            flow.poll(start["device_code"])
