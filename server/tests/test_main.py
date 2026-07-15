from server.main import access_control_warning


def test_warns_when_nobody_can_log_in():
    warning = access_control_warning(False, None, 0)
    assert warning is not None
    assert "Nobody can log in" in warning


def test_no_warning_when_registration_is_open():
    assert access_control_warning(True, None, 0) is None


def test_no_warning_when_allowlist_is_set():
    assert access_control_warning(False, frozenset({"alice"}), 0) is None


def test_no_warning_when_users_exist():
    assert access_control_warning(False, None, 2) is None
