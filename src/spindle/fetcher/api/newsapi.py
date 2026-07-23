"""NewsAPI.org — /v2/everything (requires an API key).

One request per refresh (free tier is ~100/day, results delayed ~24h). Key from
``[newsapi].api_key`` or ``$NEWSAPI_KEY``. NewsAPI gives no popularity signal,
so ranking leans on freshness + relevance.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlencode

from ...model import RawItem
from ...util import parse_date
from ..common import build_topic_query, resolve_key

ENDPOINT = "https://newsapi.org/v2/everything"
Q_MAX_LEN = 480      # NewsAPI caps `q` at 500 chars; stay just under


def fetch(ctx, cfg, log) -> List[RawItem]:
    conf = cfg.newsapi
    key = resolve_key(conf, ("NEWSAPI_KEY", "NEWSAPI_API_KEY"))
    if not key:
        log("    newsapi: no API key (set [newsapi].api_key or $NEWSAPI_KEY) — skipping")
        return []

    daily_limit = int(conf.get("daily_limit", 100))
    if not ctx.quota_ok("newsapi", daily_limit):
        log(f"    newsapi: daily limit ({daily_limit}) reached — skipping")
        return []

    # An explicit `query` wins; otherwise derive it from the configured topics.
    query = (conf.get("query") or "").strip() or build_topic_query(cfg.topics, Q_MAX_LEN)
    log(f"    newsapi: q = {query}")
    params = urlencode({
        "q": query,
        "language": conf.get("language", "en"),
        "sortBy": "publishedAt",
        "pageSize": int(conf.get("page_size", 30)),
    })
    ctx.record_request("newsapi")
    data = ctx.get_json(f"{ENDPOINT}?{params}", extra_headers={"X-Api-Key": key})
    if not isinstance(data, dict):
        return []
    if data.get("status") != "ok":
        log(f"    newsapi: {data.get('message', 'error')}")
        return []

    prefix = conf.get("prefix", "NEWS")
    items: List[RawItem] = []
    for a in data.get("articles", []):
        title = (a.get("title") or "").strip()
        url = (a.get("url") or "").strip()
        if not title or not url or title == "[Removed]":
            continue
        items.append(RawItem(
            title=title,
            url=url,
            source="newsapi",
            signal=0.0,
            published_ts=parse_date(a.get("publishedAt")),
            prefix_hint=prefix,
        ))
    return items
