"""Core data types shared across the pipeline.

Two shapes flow through the system:

* ``RawItem``  — what a fetcher emits, straight from a feed/API. Messy, may be
  missing fields, not yet deduplicated or scored.
* ``Story``    — a normalized, scored, deduplicated item. This is the ONLY
  shape that is persisted to the cache and, ultimately, rendered.

Both are plain dataclasses so they serialize to/from JSON with no ceremony.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


@dataclass
class RawItem:
    """A single item as pulled from a source, before normalization."""

    title: str
    url: str
    source: str                       # "hackernews" | "github" | "reddit" | "rss"
    signal: float = 0.0               # raw popularity signal (points / stars / score)
    published_ts: Optional[int] = None
    prefix_hint: Optional[str] = None  # display tag suggested by the source/feed
    topic_hint: Optional[str] = None
    kind: str = "story"               # "story" | "release" | "repo" | "post"
    summary: Optional[str] = None


@dataclass
class Story:
    """A processed item. This is what lives in the cache and gets displayed."""

    id: str
    prefix: str
    title: str
    url: str
    norm_url: str
    source: str
    published_ts: int = 0             # 0 == unknown
    fetched_ts: int = 0
    signal: float = 0.0
    kind: str = "story"
    topic: Optional[str] = None
    score: float = 0.0
    ai_headline: str = ""             # cheap-LLM-compressed headline (see summarizer);
                                      # empty == not summarized, render falls back to title

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Story":
        # Tolerant construction: ignore unknown keys, fill known defaults. This
        # keeps old caches loadable after the schema grows.
        allowed = Story.__dataclass_fields__.keys()  # type: ignore[attr-defined]
        return Story(**{k: v for k, v in d.items() if k in allowed})
