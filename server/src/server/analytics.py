from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from sqlite3 import Connection
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import config

logger = logging.getLogger(__name__)

BOT_USER_AGENT_PARTS = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "bingpreview",
    "curl",
    "wget",
)
DOCUMENT_EXTENSIONS = {"", ".html", ".htm"}
ASSET_EXTENSIONS = {
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp4",
    ".webm",
    ".mp3",
    ".wav",
    ".pdf",
    ".zip",
}


@dataclass(frozen=True)
class AnalyticsEvent:
    site_name: str
    path: str
    day: str
    bytes_sent: int
    is_pageview: bool
    is_not_found: bool
    visitor_hash: str | None = None
    referrer: str | None = None
    campaign: str | None = None
    country: str | None = None


def init_analytics_schema(conn: Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS analytics_daily (
        site_name TEXT NOT NULL,
        day TEXT NOT NULL,
        views INTEGER NOT NULL DEFAULT 0,
        visitors INTEGER NOT NULL DEFAULT 0,
        bytes INTEGER NOT NULL DEFAULT 0,
        not_found INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (site_name, day))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS analytics_dimensions (
        site_name TEXT NOT NULL,
        day TEXT NOT NULL,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        views INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (site_name, day, kind, value))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS analytics_visitors (
        site_name TEXT NOT NULL,
        day TEXT NOT NULL,
        visitor_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (site_name, day, visitor_hash))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_daily_site_day ON analytics_daily (site_name, day)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_dimensions_site_kind_day ON analytics_dimensions (site_name, kind, day)")


class AnalyticsStore:
    def __init__(self, conn: Connection):
        self._conn = conn

    def record(self, event: AnalyticsEvent) -> None:
        views = 1 if event.is_pageview else 0
        not_found = 1 if event.is_not_found else 0

        self._conn.execute(
            """INSERT INTO analytics_daily (site_name, day, views, bytes, not_found)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(site_name, day) DO UPDATE SET
                views = views + excluded.views,
                bytes = bytes + excluded.bytes,
                not_found = not_found + excluded.not_found""",
            (event.site_name, event.day, views, event.bytes_sent, not_found),
        )

        if event.visitor_hash:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO analytics_visitors (site_name, day, visitor_hash, created_at)
                VALUES (?, ?, ?, ?)""",
                (event.site_name, event.day, event.visitor_hash, datetime.now().isoformat()),
            )
            if cursor.rowcount:
                self._conn.execute(
                    "UPDATE analytics_daily SET visitors = visitors + 1 WHERE site_name = ? AND day = ?",
                    (event.site_name, event.day),
                )

        if event.is_pageview:
            self._increment_dimension(event.site_name, event.day, "page", event.path)
            if event.referrer:
                self._increment_dimension(event.site_name, event.day, "referrer", event.referrer)
            if event.campaign:
                self._increment_dimension(event.site_name, event.day, "campaign", event.campaign)
            if event.country:
                self._increment_dimension(event.site_name, event.day, "country", event.country)

        if event.is_not_found:
            self._increment_dimension(event.site_name, event.day, "not_found_path", event.path)

    def prune_visitors(self, retain_days: int = 2) -> None:
        cutoff = (date.today() - timedelta(days=retain_days - 1)).isoformat()
        self._conn.execute("DELETE FROM analytics_visitors WHERE day < ?", (cutoff,))

    def summary(self, site_name: str, days: int = 30) -> dict[str, Any]:
        totals = self._conn.execute(
            """SELECT
                COALESCE(SUM(views), 0) AS views,
                COALESCE(SUM(visitors), 0) AS visitors,
                COALESCE(SUM(bytes), 0) AS bytes,
                COALESCE(SUM(not_found), 0) AS not_found
            FROM analytics_daily WHERE site_name = ?""",
            (site_name,),
        ).fetchone()

        today = date.today()
        start = today - timedelta(days=days - 1)
        rows = self._conn.execute(
            """SELECT day, views, visitors FROM analytics_daily
            WHERE site_name = ? AND day >= ? AND day <= ?
            ORDER BY day ASC""",
            (site_name, start.isoformat(), today.isoformat()),
        ).fetchall()
        rows_by_day = {row["day"]: row for row in rows}
        series = []
        for offset in range(days):
            day = (start + timedelta(days=offset)).isoformat()
            row = rows_by_day.get(day)
            series.append({"day": day, "views": row["views"] if row else 0, "visitors": row["visitors"] if row else 0})

        return {
            "totals": {
                "views": totals["views"],
                "visitors": totals["visitors"],
                "bytes": totals["bytes"],
                "not_found": totals["not_found"],
            },
            "series": series,
            "top_pages": self._top_dimensions(site_name, "page", start),
            "not_found_paths": self._top_dimensions(site_name, "not_found_path", start),
            "referrers": self._top_dimensions(site_name, "referrer", start),
            "campaigns": self._top_dimensions(site_name, "campaign", start),
            "countries": self._top_dimensions(site_name, "country", start),
        }

    def total_views_by_site(self, site_names: Sequence[str]) -> dict[str, int]:
        if not site_names:
            return {}

        placeholders = ", ".join("?" for _ in site_names)
        rows = self._conn.execute(
            f"""SELECT site_name, COALESCE(SUM(views), 0) AS views
            FROM analytics_daily
            WHERE site_name IN ({placeholders})
            GROUP BY site_name""",
            tuple(site_names),
        ).fetchall()

        views = {site_name: 0 for site_name in site_names}
        views.update({row["site_name"]: row["views"] for row in rows})
        return views

    def _increment_dimension(self, site_name: str, day: str, kind: str, value: str) -> None:
        self._conn.execute(
            """INSERT INTO analytics_dimensions (site_name, day, kind, value, views)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(site_name, day, kind, value) DO UPDATE SET views = views + 1""",
            (site_name, day, kind, value[:500]),
        )

    def _top_dimensions(self, site_name: str, kind: str, start: date, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT value, SUM(views) AS views
            FROM analytics_dimensions
            WHERE site_name = ? AND kind = ? AND day >= ?
            GROUP BY value
            ORDER BY views DESC, value ASC
            LIMIT ?""",
            (site_name, kind, start.isoformat(), limit),
        ).fetchall()
        return [{"value": row["value"], "views": row["views"]} for row in rows]


class AnalyticsRecorder:
    def __init__(
        self,
        db_factory: Callable,
        max_queue_size: int = 1000,
        flush_interval: float = 1.0,
        batch_size: int = 100,
    ):
        self._db_factory = db_factory
        self._max_queue_size = max_queue_size
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._queue: asyncio.Queue[AnalyticsEvent] | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._queue = None

    def record(self, event: AnalyticsEvent | None) -> bool:
        if not event or not self._queue:
            return False
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            return False
        return True

    async def _run(self) -> None:
        try:
            while True:
                batch = await self._next_batch()
                if batch:
                    await asyncio.to_thread(self._write_batch, batch)
        except asyncio.CancelledError:
            if self._queue:
                remaining = []
                while not self._queue.empty():
                    remaining.append(self._queue.get_nowait())
                if remaining:
                    await asyncio.to_thread(self._write_batch, remaining)
            raise

    async def _next_batch(self) -> list[AnalyticsEvent]:
        if not self._queue:
            return []
        batch = []
        try:
            batch.append(await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval))
        except asyncio.TimeoutError:
            return []

        while len(batch) < self._batch_size and not self._queue.empty():
            batch.append(self._queue.get_nowait())
        return batch

    def _write_batch(self, batch: list[AnalyticsEvent]) -> None:
        try:
            with self._db_factory() as conn:
                store = AnalyticsStore(conn)
                for event in batch:
                    store.record(event)
                store.prune_visitors()
        except Exception:
            logger.warning("Failed to record analytics batch", exc_info=True)


def build_analytics_event(
    request: Any,
    site_name: str,
    path: str,
    status_code: int,
    bytes_sent: int,
    content_type: str,
    internal_hosts: Collection[str] = (),
) -> AnalyticsEvent | None:
    if request.method != "GET":
        return None
    if request.headers.get("dnt") == "1" or request.headers.get("sec-gpc") == "1":
        return None
    if _is_prefetch(request):
        return None

    path = _normalize_path(path)
    is_pageview = status_code < 400 and content_type.startswith("text/html")
    is_not_found = status_code == 404 and _is_document_request(request, path)
    if not is_pageview and not is_not_found:
        return None

    user_agent = request.headers.get("user-agent", "")
    if _is_bot(user_agent):
        return None

    day = date.today().isoformat()
    return AnalyticsEvent(
        site_name=site_name,
        path=path,
        day=day,
        bytes_sent=bytes_sent,
        is_pageview=is_pageview,
        is_not_found=is_not_found,
        visitor_hash=_visitor_hash(site_name, day, _client_ip(request), user_agent),
        referrer=_referrer_host(request, internal_hosts),
        campaign=_campaign(request),
        country=_country(request),
    )


def _normalize_path(path: str) -> str:
    parsed = urlparse(path)
    normalized = parsed.path or "/"
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _is_document_request(request: Any, path: str) -> bool:
    suffix = _suffix(path)
    if suffix in ASSET_EXTENSIONS:
        return False
    if request.headers.get("sec-fetch-dest") == "document":
        return True
    accept = request.headers.get("accept", "")
    return suffix in DOCUMENT_EXTENSIONS and (not accept or "text/html" in accept or "*/*" in accept)


def _is_prefetch(request: Any) -> bool:
    purpose = request.headers.get("purpose") or request.headers.get("sec-purpose") or ""
    lowered = purpose.lower()
    return "prefetch" in lowered or "prerender" in lowered


def _suffix(path: str) -> str:
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return ""
    return "." + last.rsplit(".", 1)[-1].lower()


def _is_bot(user_agent: str) -> bool:
    lower = user_agent.lower()
    return any(part in lower for part in BOT_USER_AGENT_PARTS)


def _client_ip(request: Any) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


def _visitor_hash(site_name: str, day: str, ip: str, user_agent: str) -> str | None:
    if not ip and not user_agent:
        return None
    secret = getattr(config, "ANALYTICS_SECRET", None)
    if not secret:
        secret = config.GITHUB_CLIENT_SECRET or secrets.token_hex(16)
        config.ANALYTICS_SECRET = secret
    raw = f"{secret}|{site_name}|{day}|{ip}|{user_agent}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _normalized_host(host: str) -> str:
    return host.lower().rstrip(".")


def _referrer_host(request: Any, internal_hosts: Collection[str]) -> str | None:
    referrer = request.headers.get("referer") or request.headers.get("referrer")
    if not referrer:
        return None
    host = _normalized_host(urlparse(referrer).hostname or "")
    current_host = _normalized_host((request.headers.get("host") or "").split(":", 1)[0])
    site_hosts = {_normalized_host(value) for value in internal_hosts}
    if host == current_host or host in site_hosts or not host:
        return None
    return host[4:] if host.startswith("www.") else host


def _campaign(request: Any) -> str | None:
    params = parse_qs(request.url.query)
    source = _first(params, "utm_source")
    medium = _first(params, "utm_medium")
    campaign = _first(params, "utm_campaign")
    parts = [part for part in (source, medium, campaign) if part]
    return " / ".join(parts) if parts else None


def _first(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    if not values:
        return None
    value = values[0].strip()
    return value[:120] if value else None


def _country(request: Any) -> str | None:
    country = request.headers.get("cf-ipcountry") or request.headers.get("x-vercel-ip-country")
    if not country:
        return None
    country = country.strip().upper()
    if len(country) != 2 or not country.isalpha() or country == "XX":
        return None
    return country
