"""Orchestration: the ``refresh``, ``sync`` and ``session-start`` workflows.

    refresh        Fetch → Normalize → Score → Deduplicate → Persist. Network.
    sync           Read cache → select next pool → write settings. Local only.
    session-start  sync (instant) + spawn a *detached* refresh if cache is stale.

Only ``refresh`` touches the network, and only ``session-start`` may spawn it —
never blocking, never on the render path.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Callable, Dict, List, Optional

from . import integration, storage, summarizer
from .config import Config
from .deduplicator import deduplicate
from .fetcher import collect
from .history import select_pool
from .model import Story
from .normalizer import normalize
from .scorer import score_all
from .util import now_ts

Logger = Callable[[str], None]
LOCK_TTL = 120  # seconds; a lock older than this is considered stale


# --------------------------------------------------------------------------- #
# Locking (prevents concurrent refreshes stampeding the network)
# --------------------------------------------------------------------------- #

class _Lock:
    def __init__(self, path: str) -> None:
        self.path = path
        self.acquired = False

    def _try_create(self) -> bool:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    def __enter__(self) -> "_Lock":
        if self._try_create():
            self.acquired = True
            return self
        # Someone holds it — steal only if clearly stale.
        try:
            age = now_ts() - int(os.path.getmtime(self.path))
        except OSError:
            age = LOCK_TTL + 1
        if age > LOCK_TTL:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.acquired = self._try_create()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.acquired:
            try:
                os.remove(self.path)
            except OSError:
                pass


def _lock_is_fresh(cache_dir: str) -> bool:
    try:
        return (now_ts() - int(os.path.getmtime(storage.lock_path(cache_dir)))) <= LOCK_TTL
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #

def is_stale(cfg: Config, meta: Optional[Dict[str, Any]] = None) -> bool:
    if meta is None:
        meta = storage.load_meta(cfg.cache_dir)
    last = int(meta.get("last_refresh_ts", 0))
    return (now_ts() - last) >= cfg.ttl_seconds


# --------------------------------------------------------------------------- #
# refresh
# --------------------------------------------------------------------------- #

def refresh(cfg: Config, log: Logger = lambda _m: None) -> Dict[str, Any]:
    storage.ensure_dir(cfg.cache_dir)
    with _Lock(storage.lock_path(cfg.cache_dir)) as lock:
        if not lock.acquired:
            log("refresh: another refresh is in progress — skipping")
            return {"skipped": True}

        now = now_ts()
        meta = storage.load_meta(cfg.cache_dir)
        existing = storage.load_stories(cfg.cache_dir)

        raw = collect(cfg, meta, log)
        fetched = normalize(raw, cfg)

        # Merge with the existing rolling store. Feeds that returned 304 (or
        # failed) contribute nothing new, but their prior stories persist here.
        merged: Dict[str, Story] = {s.id: s for s in existing}
        for s in fetched:
            prev = merged.get(s.id)
            if prev is None:
                merged[s.id] = s
                continue
            # Same story (prior cache or another source): keep the stronger
            # record as representative; on a tie prefer the fresher fetch.
            keep, other = (s, prev) if s.signal >= prev.signal else (prev, s)
            keep.signal = max(s.signal, prev.signal)
            if not keep.published_ts:
                keep.published_ts = other.published_ts or keep.published_ts
            # Carry the cached AI headline forward so we never re-summarize (a
            # freshly-normalized `s` always has an empty ai_headline).
            if not keep.ai_headline:
                keep.ai_headline = other.ai_headline
            merged[s.id] = keep
        stories = list(merged.values())

        # Drop anything past the freshness horizon.
        max_age = cfg.max_age_hours * 3600
        stories = [
            s for s in stories
            if (now - (s.published_ts or s.fetched_ts or now)) <= max_age
        ]

        score_all(stories, cfg, now)
        stories = deduplicate(stories)
        stories.sort(key=lambda s: s.score, reverse=True)
        stories = stories[: cfg.max_stories]

        # AI headline pass (optional, key-gated). Only the kept, ranked stories
        # are summarized, and only those still missing a summary — so cost stays
        # bounded and each story is summarized at most once over its lifetime.
        summarizer.summarize_stories(stories, cfg, log)

        storage.save_stories(cfg.cache_dir, stories)
        meta["last_refresh_ts"] = now
        storage.save_meta(cfg.cache_dir, meta)

        log(f"refresh: {len(fetched)} fetched, {len(stories)} cached")
        return {"cached": len(stories), "fetched": len(fetched)}


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #

def sync(cfg: Config, hook_command: Optional[str], log: Logger = lambda _m: None) -> Dict[str, Any]:
    storage.ensure_dir(cfg.cache_dir)
    stories = storage.load_stories(cfg.cache_dir)
    stories.sort(key=lambda s: s.score, reverse=True)

    history = storage.load_history(cfg.cache_dir)
    pool, new_history = select_pool(stories, history, cfg.pool_size, cfg.history_size)

    changed, verbs = integration.apply_pool(cfg, pool, hook_command)
    storage.save_history(cfg.cache_dir, new_history)

    log(f"sync: pool={len(verbs)} verbs, settings {'updated' if changed else 'unchanged'}")
    return {"pool": len(verbs), "changed": changed, "verbs": verbs}


# --------------------------------------------------------------------------- #
# session-start (the SessionStart hook entry point)
# --------------------------------------------------------------------------- #

def spawn_detached_refresh(refresh_argv: List[str], log: Logger = lambda _m: None) -> bool:
    """Fire-and-forget a refresh in a new session. Returns True if spawned."""
    try:
        subprocess.Popen(
            refresh_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # detach: survives our exit, no controlling tty
            close_fds=True,
        )
        log(f"session-start: spawned detached refresh {refresh_argv!r}")
        return True
    except Exception as e:  # never let a spawn failure surface to Claude
        log(f"session-start: refresh spawn failed ({e!r})")
        return False


def session_start(
    cfg: Config,
    hook_command: Optional[str],
    refresh_argv: List[str],
    log: Logger = lambda _m: None,
) -> Dict[str, Any]:
    # 1. Advance the pool for continuity (local, instant).
    result = sync(cfg, hook_command, log)
    # 2. Trigger a background refresh only if the cache is stale and none is
    #    already running.
    if is_stale(cfg) and not _lock_is_fresh(cfg.cache_dir):
        result["refresh_spawned"] = spawn_detached_refresh(refresh_argv, log)
    else:
        result["refresh_spawned"] = False
    return result


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

def status_info(cfg: Config) -> Dict[str, Any]:
    stories = storage.load_stories(cfg.cache_dir)
    meta = storage.load_meta(cfg.cache_dir)
    history = storage.load_history(cfg.cache_dir)
    last = int(meta.get("last_refresh_ts", 0))
    return {
        "mode": cfg.mode,
        "cache_dir": cfg.cache_dir,
        "settings_path": cfg.claude_settings_path,
        "story_count": len(stories),
        "last_refresh_ts": last,
        "age_secs": (now_ts() - last) if last else None,
        "stale": is_stale(cfg, meta),
        "history_shown": len(history.get("shown", [])),
        "history_epoch": int(history.get("epoch", 0)),
        "hook_installed": integration.hook_installed(cfg),
        "installed_verbs": integration.current_verbs(cfg),
        "top": stories[:10],
    }
