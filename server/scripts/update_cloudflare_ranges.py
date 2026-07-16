from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

SOURCES = {
    "ipv4": "https://www.cloudflare.com/ips-v4",
    "ipv6": "https://www.cloudflare.com/ips-v6",
}
OUTPUT = (
    Path(__file__).parents[1]
    / "src"
    / "server"
    / "resources"
    / "cloudflare-ip-ranges.json"
)


def fetch_ranges(url: str, version: int) -> list[str]:
    with urlopen(url, timeout=10) as response:
        values = response.read().decode("ascii").splitlines()
    networks = [ipaddress.ip_network(value, strict=True) for value in values if value]
    if not networks or any(network.version != version or not network.is_global for network in networks):
        raise RuntimeError(f"Cloudflare returned invalid IPv{version} ranges")
    return [str(network) for network in networks]


def main() -> None:
    now = datetime.now(timezone.utc)
    data = {
        "schema_version": 1,
        "version": now.date().isoformat(),
        "published_at": now.replace(microsecond=0).isoformat(),
        "sources": list(SOURCES.values()),
        "ipv4": fetch_ranges(SOURCES["ipv4"], 4),
        "ipv6": fetch_ranges(SOURCES["ipv6"], 6),
    }
    OUTPUT.write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()
