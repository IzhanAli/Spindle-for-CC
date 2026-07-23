"""Acquisition layer — a single entry point that abstracts *how* news is
gathered.

The pipeline calls :func:`collect` and never learns which backend ran. The
backend is chosen by ``cfg.mode``:

    "scraper"  → RSS/Atom + Hacker News + Reddit          (fetcher.scraper)
    "api"      → NewsAPI + GNews + DEV.to + Hacker News    (fetcher.api)

The two are mutually exclusive. Hacker News is a keyless public API and belongs
to both groups, so it lives at this level (``fetcher.hn``) and is shared.
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
        log("mode: api (NewsAPI + GNews + DEV.to + Hacker News)")
        return collect_api(ctx, cfg, log)
    from .scraper import collect as collect_scraper
    log("mode: scraper (RSS + Hacker News + Reddit)")
    return collect_scraper(ctx, cfg, log)
