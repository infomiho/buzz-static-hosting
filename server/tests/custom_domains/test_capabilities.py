from server import config
from server.custom_domains.capabilities import compute_capabilities


class ReadyControl:
    def is_ready(self):
        return True


class UnreadyControl:
    def is_ready(self):
        return False


def _range_state(error=None):
    return type("RangeState", (), {"error": error})()


def _ready_config(monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr(config, "TRAEFIK_CONTROL_TOKEN", "configured")
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_ADMISSION_ENABLED", True)
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_ROUTING_ENABLED", True)
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_INGRESS_IPS", frozenset({"8.8.8.8"}))
    monkeypatch.setattr(config, "CLOUDFLARE_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_ACTIVATION_ENABLED", True)


def _capabilities(**overrides):
    defaults = dict(
        control=None,
        diagnostician=None,
        range_state=_range_state(),
        diagnostic_runtime_ready=False,
        coordinator=None,
        automatic_admission=False,
    )
    defaults.update(overrides)
    return compute_capabilities(**defaults)


def test_disabled_when_custom_domains_off(monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", False)
    capability = _capabilities()
    assert capability.status == "disabled"
    assert capability.control_ready is False


def test_unready_without_configured_control_plane(monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr(config, "TRAEFIK_CONTROL_TOKEN", "")
    capability = _capabilities()
    assert capability.status == "unready"
    assert "control plane is not configured" in capability.detail


def test_unready_when_control_plane_not_ready(monkeypatch):
    _ready_config(monkeypatch)
    capability = _capabilities(control=UnreadyControl())
    assert capability.status == "unready"
    assert capability.detail == "Custom domain control plane is not ready"


def test_ready_when_fully_configured(monkeypatch):
    _ready_config(monkeypatch)
    capability = _capabilities(control=ReadyControl(), diagnostic_runtime_ready=True)
    assert capability.status == "ready"
    assert capability.control_ready is True
    assert capability.cloudflare_ready is True


def test_automatic_ready_requires_admission_and_coordinator(monkeypatch):
    _ready_config(monkeypatch)
    ready = dict(control=ReadyControl(), diagnostic_runtime_ready=True)
    without_coordinator = _capabilities(**ready, automatic_admission=True)
    assert without_coordinator.automatic_ready is False
    with_coordinator = _capabilities(
        **ready, automatic_admission=True, coordinator=object()
    )
    assert with_coordinator.automatic_ready is True


def test_stale_range_data_blocks_cloudflare(monkeypatch):
    _ready_config(monkeypatch)
    capability = _capabilities(
        control=ReadyControl(),
        diagnostic_runtime_ready=True,
        range_state=_range_state("range_data_stale"),
    )
    assert capability.cloudflare_ready is False
    assert capability.cloudflare_detail == "Cloudflare IP range data is stale"
