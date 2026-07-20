import json
from datetime import datetime

from server.custom_domains.probes import RANGE_PATH, load_cloudflare_ranges


def test_bundled_cloudflare_range_file_ships_and_loads():
    # The Cloudflare IP range snapshot is bundled inside the package and located
    # relative to the module. Moving the module must keep RANGE_PATH resolving;
    # this test fails loudly if it doesn't (the file backs cloudflare-mode
    # readiness, and unit tests elsewhere inject ranges rather than load them).
    assert RANGE_PATH.exists(), RANGE_PATH
    published_at = datetime.fromisoformat(json.loads(RANGE_PATH.read_text())["published_at"])
    # Pin `now` to the snapshot's own timestamp so the test never rots on the
    # 180-day staleness check while still exercising the real load path.
    ranges = load_cloudflare_ranges(now=published_at)
    assert ranges.networks
