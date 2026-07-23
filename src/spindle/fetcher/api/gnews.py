"""GNews.io — /api/v4/search (requires an API key).

One request per refresh (free tier ~100/day, max 10 articles/request). Key from
``[gnews].api_key`` or ``$GNEWS_KEY``.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlencode

from ...model import RawItem
from ...util import parse_date
from ..common import build_topic_query, resolve_key

ENDPOINT = "https://gnews.io/api/v4/search"
Q_MAX_LEN = 200      # GNews hard-caps `q` at 200 chars


def fetch(ctx, cfg, log) -> List[RawItem]:
    conf = cfg.gnews
    key = resolve_key(conf, ("GNEWS_KEY", "GNEWS_API_KEY"))
    if not key:
        log("    gnews: no API key (set [gnews].api_key or $GNEWS_KEY) — skipping")
        return []

    daily_limit = int(conf.get("daily_limit", 90))
    if not ctx.quota_ok("gnews", daily_limit):
        log(f"    gnews: daily limit ({daily_limit}) reached — skipping")
        return []

    # An explicit `query` wins; otherwise derive it from the configured topics.
    query = (conf.get("query") or "").strip() or build_topic_query(cfg.topics, Q_MAX_LEN)
    log(f"    gnews: q = {query}")
    params = urlencode({
        "q": query,
        "lang": conf.get("lang", "en"),
        "max": int(conf.get("max", 10)),
        "sortby": "publishedAt",
        "apikey": key,
    })
    ctx.record_request("gnews")
    data = ctx.get_json(f"{ENDPOINT}?{params}")
    if not isinstance(data, dict):
        return []
    if "articles" not in data:
        log(f"    gnews: {data.get('errors') or data.get('message', 'error')}")
        return []

    prefix = conf.get("prefix", "GNEWS")
    items: List[RawItem] = []
    for a in data.get("articles", []):
        title = (a.get("title") or "").strip()
        url = (a.get("url") or "").strip()
        if not title or not url:
            continue
        items.append(RawItem(
            title=title,
            url=url,
            source="gnews",
            signal=0.0,
            published_ts=parse_date(a.get("publishedAt")),
            prefix_hint=prefix,
        ))
    return items
