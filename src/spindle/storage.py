"""Cache persistence — plain JSON with atomic writes.

We deliberately store **processed stories only** (never raw RSS). Three files
live under ``cache_dir``:

    stories.json  — ranked, deduplicated Story records (the render cache)
    history.json  — which stories have already been placed (rotation state)
    meta.json     — per-feed ETag/Last-Modified + last-refresh bookkeeping

JSON (not SQLite) is the right call here: the dataset is a few hundred small
records, reads must be instant with zero setup, and a single-file store keeps
the whole tool dependency-free.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List

from .model import Story


def _p(cache_dir: str, name: str) -> str:
    return os.path.join(cache_dir, name)


def ensure_dir(cache_dir: str) -> None:
    os.makedirs(cache_dir, exist_ok=True)


def read_json(path: str, default: Any) -> Any:
    """Read JSON, returning ``default`` on any error (missing/corrupt/etc.).

    The cache must never be a source of failure — a corrupt file is treated as
    an empty cache and gets rebuilt on the next refresh.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def atomic_write_json(path: str, obj: Any) -> None:
    """Write JSON via temp-file + rename so readers never see a partial file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Typed helpers
# --------------------------------------------------------------------------- #

def load_stories(cache_dir: str) -> List[Story]:
    raw = read_json(_p(cache_dir, "stories.json"), {"stories": []})
    return [Story.from_dict(d) for d in raw.get("stories", [])]


def save_stories(cache_dir: str, stories: List[Story]) -> None:
    atomic_write_json(
        _p(cache_dir, "stories.json"),
        {"stories": [s.to_dict() for s in stories]},
    )


def load_meta(cache_dir: str) -> Dict[str, Any]:
    return read_json(_p(cache_dir, "meta.json"),
                     {"last_refresh_ts": 0, "feeds": {}})


def save_meta(cache_dir: str, meta: Dict[str, Any]) -> None:
    atomic_write_json(_p(cache_dir, "meta.json"), meta)


def load_history(cache_dir: str) -> Dict[str, Any]:
    return read_json(_p(cache_dir, "history.json"), {"shown": [], "epoch": 0})


def save_history(cache_dir: str, history: Dict[str, Any]) -> None:
    atomic_write_json(_p(cache_dir, "history.json"), history)


def lock_path(cache_dir: str) -> str:
    return _p(cache_dir, "refresh.lock")
