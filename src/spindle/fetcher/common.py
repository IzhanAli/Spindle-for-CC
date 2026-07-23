"""Shared acquisition-layer infrastructure used by both provider groups.

``FetchContext`` wraps the HTTP client + per-feed conditional-request state and
is passed to every provider. ``safe`` isolates a provider so one failure can't
sink a whole refresh. ``resolve_key`` finds an API key from config or env.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from ..http import fetch
from ..util import now_ts

Logger = Callable[[str], None]


def _utc_date() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class FetchContext:
    """HTTP client + on-disk conditional-request bookkeeping."""

    def __init__(self, cfg, meta: Dict[str, Any], log: Logger = lambda _m: None) -> None:
        self.cfg = cfg
        self.meta = meta
        self.log = log
        self.feeds: Dict[str, Any] = meta.setdefault("feeds", {})

    def conditional_get(self, url: str, extra_headers: Optional[Dict[str, str]] = None) -> Optional[bytes]:
        """GET with ETag/If-Modified-Since. Returns bytes, or ``None`` when the
        feed is unchanged (304) or unreachable."""
        entry = self.feeds.get(url, {})
        res = fetch(
            url,
            timeout=self.cfg.request_timeout_secs,
            user_agent=self.cfg.user_agent,
            max_bytes=self.cfg.max_feed_bytes,
            etag=entry.get("etag"),
            last_modified=entry.get("last_modified"),
            extra_headers=extra_headers,
            ca_bundle=self.cfg.ca_bundle,
        )
        if res.ok:
            self.feeds[url] = {
                "etag": res.etag,
                "last_modified": res.last_modified,
                "fetched_ts": now_ts(),
            }
            return res.body
        if res.error:
            self.log(f"    ! {url}: {res.error}")
        return None

    # -- daily request quota (for keyed APIs with free-tier caps) -----------

    def quota_ok(self, source: str, daily_limit: int) -> bool:
        """True if ``source`` still has budget today. ``daily_limit`` <= 0 = unlimited."""
        if not daily_limit or daily_limit <= 0:
            return True
        q = self.meta.setdefault("quota", {}).get(source)
        if q and q.get("date") == _utc_date():
            return int(q.get("count", 0)) < int(daily_limit)
        return True

    def record_request(self, source: str) -> None:
        quota = self.meta.setdefault("quota", {})
        q = quota.setdefault(source, {})
        if q.get("date") != _utc_date():
            q["date"] = _utc_date()
            q["count"] = 0
        q["count"] = int(q.get("count", 0)) + 1

    def get_json(self, url: str, extra_headers: Optional[Dict[str, str]] = None) -> Optional[Any]:
        res = fetch(
            url,
            timeout=self.cfg.request_timeout_secs,
            user_agent=self.cfg.user_agent,
            max_bytes=self.cfg.max_feed_bytes,
            extra_headers=extra_headers,
            ca_bundle=self.cfg.ca_bundle,
        )
        if not res.ok:
            if res.error:
                self.log(f"    ! {url}: {res.error}")
            return None
        try:
            return json.loads(res.body.decode("utf-8", "replace"))
        except ValueError:
            return None


def resolve_key(table: Dict[str, Any], env_names) -> str:
    """API key from ``table['api_key']`` or the first set env var, else ''."""
    key = (table.get("api_key") or "").strip()
    if key:
        return key
    for name in env_names:
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    return ""


def build_topic_query(topics, max_len: int) -> str:
    """Build an ``OR``-joined search query from ``topics``, within ``max_len``.

    Multi-word topics are quoted as exact phrases (``"React Native"``); terms are
    added in the list's priority order (mobile-first) and, once the next term
    would exceed the length budget, the rest are dropped. Both NewsAPI and GNews
    accept this ``OR`` / quoted-phrase syntax — GNews caps ``q`` at 200 chars,
    NewsAPI at 500 — so callers pass the appropriate ``max_len``.
    """
    parts: List[str] = []
    length = 0
    sep = " OR "
    for topic in topics or []:
        term = (topic or "").strip()
        if not term:
            continue
        if " " in term:
            term = '"' + term.replace('"', "") + '"'
        add = len(term) + (len(sep) if parts else 0)
        if length + add > max_len:
            break  # priority-ordered: keep the leading terms that fit
        parts.append(term)
        length += add
    return sep.join(parts)


def safe(name: str, fn, ctx: "FetchContext", cfg, log: Logger) -> List:
    """Run a provider, isolating any exception into an empty result."""
    try:
        items = fn(ctx, cfg, log)
        log(f"  {name}: {len(items)} items")
        return items
    except Exception as e:
        log(f"  {name}: skipped ({e!r})")
        return []
