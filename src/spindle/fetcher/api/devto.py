"""DEV.to — /api/articles (no API key).

Fetches the top articles of the last N days per configured tag (one request per
tag). ``positive_reactions_count`` is used as the popularity signal.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlencode

from ...model import RawItem
from ...util import parse_date
from ..common import resolve_key  # noqa: F401 (kept for symmetry / future auth)

ENDPOINT = "https://dev.to/api/articles"


def fetch(ctx, cfg, log) -> List[RawItem]:
    conf = cfg.devto
    prefix = conf.get("prefix", "DEV")
    per_page = int(conf.get("per_page", 15))
    top_days = int(conf.get("top_days", 7))
    min_reactions = int(conf.get("min_reactions", 0))
    tags = conf.get("tags") or [None]     # None → general top feed

    items: List[RawItem] = []
    seen: set = set()
    for tag in tags:
        query = {"per_page": per_page, "top": top_days}
        if tag:
            query["tag"] = tag
        data = ctx.get_json(f"{ENDPOINT}?{urlencode(query)}")
        if not isinstance(data, list):
            continue
        for a in data:
            title = (a.get("title") or "").strip()
            url = (a.get("url") or "").strip()
            if not title or not url or url in seen:
                continue
            reactions = int(a.get("positive_reactions_count") or 0)
            if reactions < min_reactions:
                continue
            seen.add(url)
            items.append(RawItem(
                title=title,
                url=url,
                source="devto",
                signal=float(reactions),
                published_ts=parse_date(a.get("published_timestamp")),
                prefix_hint=prefix,
                topic_hint=tag,
            ))
    return items
