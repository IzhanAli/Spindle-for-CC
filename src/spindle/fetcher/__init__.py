"""Acquisition layer — a single entry point that abstracts *how* news is
gathered.

The pipeline calls :func:`collect` and never learns which backend ran. The
backend is chosen by ``cfg.mode``:

    "scraper"  → RSS/Atom + Hacker News + Lobsters + Reddit          (fetcher.scraper)
    "api"      → NewsAPI + GNews + DEV.to + Hacker News + Lobsters    (fetcher.api)

The two are mutually exclusive. Hacker News and Lobsters are keyless public
APIs and belong to both groups, so they live at this level (``fetcher.hn``,
``fetcher.lobsters``) and are shared.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..model import RawItem
from .common import FetchContext, Logger, resolve_key, safe  # re-exported

__all__ = ["collect", "FetchContext", "Logger", "resolve_key", "safe"]


def collect(cfg, meta: Dict[str, Any], log: Logger = lambda _m: None) -> List[RawItem]:
    ctx = FetchContext(cfg, meta, log)
    mode = (cfg.mode or "scraper").strip().lower()
    if mode == "api":
        from .api import collect as collect_api
        log("mode: api (NewsAPI + GNews + DEV.to + Hacker News + Lobsters)")
        return collect_api(ctx, cfg, log)
    from .scraper import collect as collect_scraper
    log("mode: scraper (RSS + Hacker News + Lobsters + Reddit)")
    return collect_scraper(ctx, cfg, log)
