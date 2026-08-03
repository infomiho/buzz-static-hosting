"""The one Jinja environment.

Every router renders through this instance. Building a separate `Jinja2Templates`
per router silently drops shared globals: an undefined global renders as the
empty string, so a fingerprint or feature flag registered on one environment
disappears on pages served by another.
"""

import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates

from . import __version__

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def _asset_version() -> str:
    """Fingerprint the built stylesheet so an upgrade cannot leave a browser or
    an intermediary proxy serving the previous one from cache."""
    stylesheet = STATIC_DIR / "style.css"
    if not stylesheet.is_file():
        return "dev"
    return hashlib.sha256(stylesheet.read_bytes()).hexdigest()[:12]


templates = Jinja2Templates(directory=TEMPLATE_DIR)
templates.env.globals["asset_version"] = _asset_version()
templates.env.globals["server_version"] = __version__
