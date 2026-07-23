"""Hacker News via the public Algolia search API (front page, no auth)."""

from __future__ import annotations

from typing import List

from ..model import RawItem

API = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage={n}"
ITEM_URL = "https://news.ycombinator.com/item?id={id}"


def fetch(ctx, cfg, log) -> List[RawItem]:
    hn = cfg.hackernews
    limit = int(hn.get("limit", 30))
    min_points = int(hn.get("min_points", 0))
    prefix = hn.get("prefix", "HN")

    data = ctx.get_json(API.format(n=limit))
    if not isinstance(data, dict):
        return []

    items: List[RawItem] = []
    for hit in data.get("hits", []):
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        points = int(hit.get("points") or 0)
        if points < min_points:
            continue
        object_id = hit.get("objectID")
        url = hit.get("url") or ITEM_URL.format(id=object_id)
        items.append(RawItem(
            title=title,
            url=url,
            source="hackernews",
            signal=float(points),
            published_ts=hit.get("created_at_i"),
            prefix_hint=prefix,
        ))
    return items
