import pytest

from server.device_authorization import (
    DEVICE_CODE_TTL_SECONDS,
    DeviceAuthorizationService,
    DeviceCodeExpired,
    normalize_user_code,
)
from server.pending_store import PendingStore

from tests.passkey_helpers import FakeClock

VERIFICATION_URI = "https://buzz.example/device"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service(clock):
    return DeviceAuthorizationService(
        store=PendingStore(clock=clock), verification_uri=VERIFICATION_URI
    )


def test_start_shapes_the_authorization(service):
    grant = service.start()
    assert grant.verification_uri == VERIFICATION_URI
    assert len(grant.user_code) == 9
    assert grant.expires_in == DEVICE_CODE_TTL_SECONDS


def test_poll_pending_then_approved(service):
    grant = service.start()
    assert service.poll(grant.device_code) is None
    assert service.approve(grant.user_code, user_id=7)
    assert service.poll(grant.device_code) == 7


def test_grant_is_single_use(service):
    grant = service.start()
    service.approve(grant.user_code, user_id=7)
    service.poll(grant.device_code)
    with pytest.raises(DeviceCodeExpired):
        service.poll(grant.device_code)
    assert not service.approve(grant.user_code, user_id=7)


def test_unknown_device_code_reads_as_expired(service):
    with pytest.raises(DeviceCodeExpired):
        service.poll("unknown")


def test_unknown_user_code_is_not_approved(service):
    service.start()
    assert not service.approve("BBBB-BBBB", user_id=7)


def test_already_approved_grant_cannot_be_restamped(service):
    grant = service.start()
    assert service.approve(grant.user_code, user_id=7)
    assert not service.approve(grant.user_code, user_id=99)
    assert service.poll(grant.device_code) == 7


def test_codes_expire(service, clock):
    grant = service.start()
    clock.advance(DEVICE_CODE_TTL_SECONDS + 1)
    assert not service.approve(grant.user_code, user_id=7)
    with pytest.raises(DeviceCodeExpired):
        service.poll(grant.device_code)


def test_approve_accepts_messy_user_input(service):
    grant = service.start()
    messy = f"  {grant.user_code.replace('-', ' ').lower()} "
    assert service.approve(messy, user_id=7)


def test_normalize_rejects_wrong_length_or_characters():
    assert normalize_user_code("BBBB-BBB") == ""
    assert normalize_user_code("AAAA-EEEE") == ""
    assert normalize_user_code("bcdf ghjk") == "BCDF-GHJK"
