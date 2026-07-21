from server.custom_domains.capabilities import compute_capabilities


class ReadyControl:
    def is_ready(self):
        return True


class UnreadyControl:
    def is_ready(self):
        return False


def _range_state(error=None):
    return type("RangeState", (), {"error": error})()


def _capabilities(**overrides):
    defaults = dict(
        enabled=True,
        control_token="configured",
        ingress_ips=frozenset({"8.8.8.8"}),
        control=None,
        diagnostician=None,
        range_state=_range_state(),
        diagnostic_runtime_ready=False,
        coordinator=None,
    )
    defaults.update(overrides)
    return compute_capabilities(**defaults)


def test_disabled_when_custom_domains_off():
    capability = _capabilities(enabled=False)
    assert capability.status == "disabled"
    assert capability.control_ready is False
    assert capability.automatic_ready is False


def test_unready_without_configured_control_plane():
    capability = _capabilities(control_token="")
    assert capability.status == "unready"
    assert "control plane is not configured" in capability.detail


def test_unready_when_control_plane_not_ready():
    capability = _capabilities(control=UnreadyControl())
    assert capability.status == "unready"
    assert capability.detail == "Custom domain control plane is not ready"


def test_unready_without_ingress():
    capability = _capabilities(
        ingress_ips=frozenset(), control=ReadyControl(), diagnostic_runtime_ready=True
    )
    assert capability.status == "unready"
    assert "routing is not configured" in capability.detail


def test_ready_when_fully_configured():
    capability = _capabilities(
        control=ReadyControl(), diagnostic_runtime_ready=True, coordinator=object()
    )
    assert capability.status == "ready"
    assert capability.control_ready is True
    assert capability.cloudflare_ready is True
    assert capability.automatic_ready is True


def test_automatic_ready_requires_coordinator():
    ready = dict(control=ReadyControl(), diagnostic_runtime_ready=True)
    assert _capabilities(**ready, coordinator=None).automatic_ready is False
    assert _capabilities(**ready, coordinator=object()).automatic_ready is True


def test_automatic_ready_without_cloudflare():
    # The core decoupling: a server whose Cloudflare support is unavailable
    # (stale/missing ranges) still offers automatic onboarding for direct domains.
    capability = _capabilities(
        control=ReadyControl(),
        diagnostic_runtime_ready=True,
        coordinator=object(),
        range_state=_range_state("range_data_stale"),
    )
    assert capability.cloudflare_ready is False
    assert capability.automatic_ready is True


def test_stale_range_data_blocks_cloudflare():
    capability = _capabilities(
        control=ReadyControl(),
        diagnostic_runtime_ready=True,
        range_state=_range_state("range_data_stale"),
    )
    assert capability.cloudflare_ready is False
    assert capability.cloudflare_detail == "Cloudflare IP range data is stale"
