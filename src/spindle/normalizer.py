"""Turn raw source items into clean, uniform ``Story`` records.

Responsibilities: sanitize the title, canonicalize the URL, assign a stable id
and a display prefix, and stamp fetch time. Scoring and deduplication happen in
later stages.
"""

from __future__ import annotations

from typing import List

from .model import RawItem, Story
from .util import normalize_url, now_ts, sanitize_text, story_id

_SOURCE_PREFIX = {
    "hackernews": "HN",
    "reddit": "RDT",
    "rss": "DEV",
    "newsapi": "NEWS",
    "gnews": "GNEWS",
    "devto": "DEV",
}


def _prefix_for(raw: RawItem) -> str:
    if raw.prefix_hint:
        return raw.prefix_hint.strip()
    return _SOURCE_PREFIX.get(raw.source, "DEV")


def normalize(raw_items: List[RawItem], cfg) -> List[Story]:
    now = now_ts()
    stories: List[Story] = []
    for r in raw_items:
        title = sanitize_text(r.title)
        if not title:
            continue
        norm = normalize_url(r.url or "")
        stories.append(Story(
            id=story_id(norm, title),
            prefix=_prefix_for(r),
            title=title,
            url=(r.url or "").strip(),
            norm_url=norm,
            source=r.source,
            published_ts=int(r.published_ts or 0),
            fetched_ts=now,
            signal=float(r.signal or 0.0),
            kind=r.kind,
            topic=r.topic_hint,
        ))
    return stories
