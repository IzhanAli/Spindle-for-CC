"""Lobsters via its public JSON (the hottest stories, no auth).

A computing-focused link aggregator with a high signal-to-noise ratio. Like
Hacker News it needs no key, so both modes use it. Link posts point at the
article, text posts at their discussion.
"""

from __future__ import annotations

from typing import List

from ..model import RawItem
from ..util import parse_date

HOTTEST = "https://lobste.rs/hottest.json"


def fetch(ctx, cfg, log) -> List[RawItem]:
    lb = cfg.lobsters
    limit = int(lb.get("limit", 25))
    min_score = int(lb.get("min_score", 0))
    prefix = lb.get("prefix", "LOB")

    data = ctx.get_json(HOTTEST)
    if not isinstance(data, list):
        return []

    items: List[RawItem] = []
    for story in data[:limit]:
        if not isinstance(story, dict):
            continue
        title = (story.get("title") or "").strip()
        if not title:
            continue
        score = int(story.get("score") or 0)
        if score < min_score:
            continue
        tags = story.get("tags") or []
        items.append(RawItem(
            title=title,
            url=story.get("url") or story.get("comments_url") or "",
            source="lobsters",
            signal=float(score),
            published_ts=parse_date(story.get("created_at")),
            prefix_hint=prefix,
            topic_hint=tags[0] if tags and isinstance(tags[0], str) else None,
            summary=story.get("description"),       # text posts; links have none
        ))
    return items
