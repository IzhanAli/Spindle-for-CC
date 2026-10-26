"""Turn raw source items into clean, uniform ``Story`` records.

Responsibilities: sanitize the title, trim the feed's description to a short
summary, canonicalize the URL, assign a stable id and a display prefix, stamp
fetch time, and drop items that aren't news at all (package-registry release
pages). Scoring and deduplication happen in later stages.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlsplit

from .model import RawItem, Story
from .util import excerpt, normalize_url, now_ts, sanitize_text, story_id

_SOURCE_PREFIX = {
    "hackernews": "HN",
    "lobsters": "LOB",
    "reddit": "RDT",
    "rss": "DEV",
    "newsapi": "NEWS",
    "gnews": "GNEWS",
    "devto": "DEV",
}


# Package-registry release pages ("pycanha 0.23.0" on pypi.org) that NewsAPI and
# GNews index as articles. They're a firehose of obscure version bumps with no
# story behind them, so they never reach the spinner. host → listing path prefix.
_REGISTRY_LISTINGS = {
    "pypi.org": "/project/",
    "npmjs.com": "/package/",
    "crates.io": "/crates/",
    "rubygems.org": "/gems/",
    "nuget.org": "/packages/",
    "packagist.org": "/packages/",
    "pub.dev": "/packages/",
    "hex.pm": "/packages/",
    "pkg.go.dev": "/",
    "libraries.io": "/",
}


def is_registry_listing(url: str) -> bool:
    """True if ``url`` is a package page on a registry rather than an article."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    prefix = _REGISTRY_LISTINGS.get(host)
    return prefix is not None and (parts.path or "").startswith(prefix)


def _prefix_for(raw: RawItem) -> str:
    if raw.prefix_hint:
        return raw.prefix_hint.strip()
    return _SOURCE_PREFIX.get(raw.source, "DEV")


def normalize(raw_items: List[RawItem], cfg) -> List[Story]:
    now = now_ts()
    stories: List[Story] = []
    for r in raw_items:
        title = sanitize_text(r.title)
        if not title or is_registry_listing(r.url):
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
            summary=excerpt(r.summary, title),
        ))
    return stories
