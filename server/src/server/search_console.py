from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from google.auth import crypt, jwt

from . import config

logger = logging.getLogger(__name__)

SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKEN_EXPIRY_MARGIN_SECONDS = 60


class SearchConsoleError(Exception):
    pass


class SearchConsoleClient(Protocol):
    def query_search_terms(
        self, site_host: str, start: date, end: date, limit: int = 10
    ) -> list[dict[str, Any]]: ...


def build_search_terms_payload(site_host: str, start: date, end: date, limit: int) -> dict[str, Any]:
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["query"],
        "rowLimit": limit,
        "dimensionFilterGroups": [{
            "filters": [{
                "dimension": "page",
                "operator": "contains",
                "expression": f"://{site_host}/",
            }],
        }],
    }


def map_search_terms_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "term": row["keys"][0],
            "clicks": round(row.get("clicks", 0)),
            "impressions": round(row.get("impressions", 0)),
            "ctr": round(row.get("ctr", 0) * 100, 1),
            "position": round(row.get("position", 0), 1),
        }
        for row in rows
        if row.get("keys")
    ]


def load_service_account_credentials(value: str) -> dict[str, Any]:
    """Accepts either the JSON key content or a path to the key file."""
    text = value.strip()
    if not text.startswith("{"):
        text = Path(text).read_text()
    credentials = json.loads(text)
    if "client_email" not in credentials or "private_key" not in credentials:
        raise ValueError("Service account key must contain client_email and private_key")
    return credentials


def create_search_console_client() -> HttpSearchConsoleClient | None:
    if not config.GSC_CREDENTIALS:
        return None
    property_url = config.GSC_PROPERTY or (f"sc-domain:{config.DOMAIN}" if config.DOMAIN else None)
    if not property_url:
        logger.error("BUZZ_GSC_CREDENTIALS is set but BUZZ_GSC_PROPERTY and BUZZ_DOMAIN are not; search terms disabled")
        return None
    try:
        credentials = load_service_account_credentials(config.GSC_CREDENTIALS)
    except (OSError, ValueError) as exc:
        logger.error("Failed to load Search Console credentials: %s; search terms disabled", exc)
        return None
    return HttpSearchConsoleClient(credentials, property_url)


class HttpSearchConsoleClient:
    def __init__(self, credentials: dict[str, Any], property_url: str):
        self._client_email = credentials["client_email"]
        self._signer = crypt.RSASigner.from_service_account_info(credentials)
        self._property = property_url
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def query_search_terms(
        self, site_host: str, start: date, end: date, limit: int = 10
    ) -> list[dict[str, Any]]:
        payload = build_search_terms_payload(site_host, start, end, limit)
        url = (
            "https://www.googleapis.com/webmasters/v3/sites/"
            f"{quote(self._property, safe='')}/searchAnalytics/query"
        )
        response = self._post_json(url, payload, self._get_access_token())
        return map_search_terms_rows(response.get("rows", []))

    def _get_access_token(self) -> str:
        with self._token_lock:
            if self._access_token and time.time() < self._token_expires_at - TOKEN_EXPIRY_MARGIN_SECONDS:
                return self._access_token
            response = self._fetch_access_token()
            self._access_token = response["access_token"]
            self._token_expires_at = time.time() + response.get("expires_in", 3600)
            return self._access_token

    def _fetch_access_token(self) -> dict[str, Any]:
        now = int(time.time())
        assertion = jwt.encode(self._signer, {
            "iss": self._client_email,
            "scope": SEARCH_CONSOLE_SCOPE,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }).decode()
        req = Request(
            TOKEN_URL,
            data=urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        return self._read_json(req, "token request")

    def _post_json(self, url: str, payload: dict[str, Any], access_token: str) -> dict[str, Any]:
        req = Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._read_json(req, "search terms query")

    def _read_json(self, req: Request, action: str) -> dict[str, Any]:
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            logger.warning("Search Console %s failed with status %s: %s", action, exc.code, detail)
            raise SearchConsoleError(f"Search Console {action} failed with status {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("Search Console %s failed: %s", action, exc)
            raise SearchConsoleError(f"Search Console {action} failed") from exc


class FakeSearchConsoleClient:
    def __init__(self) -> None:
        self.terms: list[dict[str, Any]] = [
            {"term": "static site hosting", "clicks": 12, "impressions": 340, "ctr": 3.5, "position": 8.2},
        ]
        self.calls: list[dict[str, Any]] = []

    def query_search_terms(
        self, site_host: str, start: date, end: date, limit: int = 10
    ) -> list[dict[str, Any]]:
        self.calls.append({"site_host": site_host, "start": start, "end": end, "limit": limit})
        return self.terms
