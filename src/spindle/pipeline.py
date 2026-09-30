"""Orchestration: the ``refresh``, ``clean``, ``sync`` and ``session-start``
workflows.

    refresh        Fetch → Normalize → Score → Deduplicate → Clean → Persist.
    clean          Re-run headline cleaning on stories still waiting for it.
    sync           Read cache → select next pool → write settings. Local only.
    session-start  sync (instant) + spawn a *detached* refresh if the cache is
                   stale, or a detached clean if stories are waiting for one.

Nobody ever waits on the slow parts: the network and the headline agent only
run in detached processes, which sync when they finish so fresh headlines reach
the spinner mid-session (settings hot-reload).
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
LOCK_TTL = 600  # seconds; a lock older than this is considered stale (covers
                # feed fetches plus a few headline-agent calls and retries)
CLEAN_RETRY_SECS = 300  # min gap between session-start retries of held-back stories


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

        # Headline cleaning pass (mandatory for display). Only the kept, ranked
        # stories are cleaned, and only those still missing a label — so usage
        # stays bounded and each story is cleaned at most once in its lifetime.
        summarizer.summarize_stories(stories, cfg, log)

        storage.save_stories(cfg.cache_dir, stories)
        meta["last_refresh_ts"] = now
        meta["last_clean_ts"] = now
        storage.save_meta(cfg.cache_dir, meta)

        log(f"refresh: {len(fetched)} fetched, {len(stories)} cached")
        return {"cached": len(stories), "fetched": len(fetched)}


# --------------------------------------------------------------------------- #
# clean
# --------------------------------------------------------------------------- #

def needs_clean(cfg: Config, meta: Optional[Dict[str, Any]] = None) -> bool:
    """True if stories are waiting for cleaning and the last try isn't recent."""
    if meta is None:
        meta = storage.load_meta(cfg.cache_dir)
    if now_ts() - int(meta.get("last_clean_ts", 0)) < CLEAN_RETRY_SECS:
        return False
    return any(not s.ai_headline for s in storage.load_stories(cfg.cache_dir))


def clean(cfg: Config, log: Logger = lambda _m: None) -> Dict[str, Any]:
    """Clean headlines the last refresh couldn't — no network fetch."""
    storage.ensure_dir(cfg.cache_dir)
    with _Lock(storage.lock_path(cfg.cache_dir)) as lock:
        if not lock.acquired:
            log("clean: a refresh is in progress — skipping")
            return {"skipped": True}
        stories = storage.load_stories(cfg.cache_dir)
        n = summarizer.summarize_stories(stories, cfg, log)
        if n:
            storage.save_stories(cfg.cache_dir, stories)
        meta = storage.load_meta(cfg.cache_dir)
        meta["last_clean_ts"] = now_ts()
        storage.save_meta(cfg.cache_dir, meta)
        return {"cleaned": n}


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #

def sync(cfg: Config, log: Logger = lambda _m: None) -> Dict[str, Any]:
    storage.ensure_dir(cfg.cache_dir)
    # Only agent-cleaned headlines are eligible; uncleaned stories wait for a
    # refresh to clean them. With none cleaned, no verbs are written and the
    # spinner keeps whatever it had.
    stories = [s for s in storage.load_stories(cfg.cache_dir) if s.ai_headline]
    stories.sort(key=lambda s: s.score, reverse=True)

    history = storage.load_history(cfg.cache_dir)
    pool, new_history = select_pool(stories, history, cfg.pool_size, cfg.history_size)

    changed, verbs = integration.apply_pool(cfg, pool)
    storage.save_history(cfg.cache_dir, new_history)

    log(f"sync: pool={len(verbs)} verbs, settings {'updated' if changed else 'unchanged'}")
    return {"pool": len(verbs), "changed": changed, "verbs": verbs}


# --------------------------------------------------------------------------- #
# session-start (the SessionStart hook entry point)
# --------------------------------------------------------------------------- #

def _detach_options() -> List[Dict[str, Any]]:
    """Popen options that let the child outlive the hook, best first.

    POSIX: a new session, so the child has no controlling tty and isn't in the
    hook's process group. Windows has no sessions: detach from the console and,
    if the parent's job object allows it, break away from the job so the child
    isn't killed along with the hook's process tree.
    """
    if os.name != "nt":
        return [{"start_new_session": True}]
    base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return [
        {"creationflags": base | subprocess.CREATE_BREAKAWAY_FROM_JOB},
        {"creationflags": base},      # the job forbids breakaway
    ]


def spawn_detached(argv: List[str], log: Logger = lambda _m: None) -> bool:
    """Fire-and-forget ``argv`` so it survives our exit. Returns True if spawned."""
    for opts in _detach_options():
        try:
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                **opts,
            )
            log(f"spawned detached {argv!r}")
            return True
        except Exception as e:  # never let a spawn failure surface to Claude
            log(f"spawn failed ({e!r})")
    return False


def spawn_refresh(cfg: Config, spindle_argv: List[str], log: Logger = lambda _m: None) -> bool:
    """Start a detached ``refresh --sync`` unless one is already running."""
    if _lock_is_fresh(cfg.cache_dir):
        return False
    return spawn_detached(spindle_argv + ["refresh", "--sync"], log)


def session_start(
    cfg: Config,
    spindle_argv: List[str],
    log: Logger = lambda _m: None,
) -> Dict[str, Any]:
    """``spindle_argv`` re-invokes this tool; subcommands are appended to it."""
    # 0. The headline-writer's own headless session must not rotate the pool
    #    or spawn another refresh (see summarizer.CHILD_ENV).
    if os.environ.get(summarizer.CHILD_ENV):
        return {"skipped": True}
    # 1. Advance the pool for continuity (local, instant).
    result = sync(cfg, log)
    # 2. Kick off the slow work in the background, never more than one at once:
    #    a refresh if the cache is stale, else a clean for held-back stories.
    result["spawned"] = None
    if not _lock_is_fresh(cfg.cache_dir):
        meta = storage.load_meta(cfg.cache_dir)
        if is_stale(cfg, meta):
            if spawn_detached(spindle_argv + ["refresh", "--sync"], log):
                result["spawned"] = "refresh"
        elif needs_clean(cfg, meta):
            if spawn_detached(spindle_argv + ["clean", "--sync"], log):
                result["spawned"] = "clean"
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
        "cleaned_count": sum(1 for s in stories if s.ai_headline),
        "last_refresh_ts": last,
        "age_secs": (now_ts() - last) if last else None,
        "stale": is_stale(cfg, meta),
        "refreshing": _lock_is_fresh(cfg.cache_dir),
        "history_shown": len(history.get("shown", [])),
        "history_epoch": int(history.get("epoch", 0)),
        "legacy_hook": integration.legacy_hook_installed(cfg),
        "claude_bin": summarizer.find_claude(cfg.ai or {}),
        "installed_verbs": integration.current_verbs(cfg),
        "top": stories[:10],
    }
