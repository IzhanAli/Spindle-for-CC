"""Scraper backend: RSS/Atom feeds + Hacker News (Algolia) + Reddit.

No API keys. Everything is a public feed or public JSON endpoint.
"""

from __future__ import annotations

from typing import List

from ...model import RawItem
from ..common import FetchContext, Logger, safe


def collect(ctx: FetchContext, cfg, log: Logger) -> List[RawItem]:
    from . import rss, reddit
    from .. import hn                    # shared, keyless HN provider

    items: List[RawItem] = []
    items += safe("rss", rss.fetch, ctx, cfg, log)
    if cfg.reddit.get("enabled", True):
        items += safe("reddit", reddit.fetch, ctx, cfg, log)
    if cfg.hackernews.get("enabled", True):
        items += safe("hackernews", hn.fetch, ctx, cfg, log)
    return items
