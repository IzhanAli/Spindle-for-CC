"""API backend: NewsAPI + GNews (both keyed) + DEV.to + Hacker News (keyless).

Keyed sources without a key are skipped (with a log line), so this mode still
works on DEV.to + Hacker News alone.
"""

from __future__ import annotations

from typing import List

from ...model import RawItem
from ..common import FetchContext, Logger, safe


def collect(ctx: FetchContext, cfg, log: Logger) -> List[RawItem]:
    from . import newsapi, gnews, devto
    from .. import hn                    # shared, keyless HN provider

    items: List[RawItem] = []
    if cfg.newsapi.get("enabled", True):
        items += safe("newsapi", newsapi.fetch, ctx, cfg, log)
    if cfg.gnews.get("enabled", True):
        items += safe("gnews", gnews.fetch, ctx, cfg, log)
    if cfg.devto.get("enabled", True):
        items += safe("devto", devto.fetch, ctx, cfg, log)
    if cfg.hackernews.get("enabled", True):
        items += safe("hackernews", hn.fetch, ctx, cfg, log)
    return items
