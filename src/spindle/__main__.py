"""Command-line interface — what the plugin's hook and skills call.

    spindle refresh [--sync] [--background]
                              fetch + rebuild the cache (network); --sync also
                              writes the new pool to settings right away;
                              --background detaches and returns immediately
    spindle clean [--sync]    clean headlines still waiting for the agent
    spindle sync              advance the pool and write settings (local)
    spindle session-start     SessionStart hook entry point (sync + maybe refresh)
    spindle status            show cache & integration status
    spindle reset [--all]     reset rotation history (and optionally the cache)
    spindle uninstall         remove spinnerVerbs, the cache, and pre-plugin leftovers
    spindle version

Global flags (--config, --mode, -v) work before or after the subcommand.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from typing import List, Optional

from . import __version__
from . import config as config_mod
from . import integration, pipeline, storage

_SUPPRESS = argparse.SUPPRESS

# Where the pre-plugin curl installer put its checkout and launcher symlink.
_LEGACY_CLONE = os.path.expanduser("~/.local/share/spindle-claude-code")
_LEGACY_LAUNCHER = os.path.expanduser("~/.local/bin/spindle")


def _make_logger(verbose: bool):
    def log(msg: str) -> None:
        if verbose:
            print(msg, file=sys.stderr)
    return log


def _reinvoke_argv(explicit_config: Optional[str]) -> List[str]:
    """The argv that re-runs this tool with the current interpreter."""
    argv0 = os.path.abspath(sys.argv[0])
    if os.path.isfile(argv0):
        base = [sys.executable, argv0]
    else:                              # launched via `python -m spindle`
        base = [sys.executable, "-m", "spindle"]
    if explicit_config:
        base += ["--config", os.path.abspath(explicit_config)]
    return base


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _fmt_age(secs: Optional[int]) -> str:
    if secs is None:
        return "never"
    if secs < 90:
        return f"{secs}s ago"
    if secs < 5400:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"


def _cmd_status(cfg) -> int:
    info = pipeline.status_info(cfg)
    print(f"mode            {info['mode']}")
    print(f"cache dir       {info['cache_dir']}")
    print(f"settings        {info['settings_path']}")
    print(f"stories cached  {info['story_count']} ({info['cleaned_count']} cleaned)")
    state = "refreshing now" if info["refreshing"] else ("STALE" if info["stale"] else "fresh")
    print(f"last refresh    {_fmt_age(info['age_secs'])}  ({state})")
    print(f"rotation        {info['history_shown']} shown, epoch {info['history_epoch']}")
    print(f"headline agent  {info['claude_bin'] or 'claude not found — spinner will not update'}")
    if info["legacy_hook"]:
        print("legacy hook     yes (old curl install still registered — see README › Upgrading)")
    verbs = info["installed_verbs"]
    print(f"verbs in pool   {len(verbs)}")
    for v in verbs[:8]:
        print(f"    {v}…")
    if info["top"]:
        print("top stories:  (ai = cleaned; untagged ones wait for the next refresh)")
        for s in info["top"]:
            shown = s.ai_headline or s.title
            tag = "ai" if s.ai_headline else "  "
            print(f"    {s.score:5.2f} {tag} {s.prefix:>7}  {shown[:68]}")
    return 0


def _cmd_reset(cfg, reset_all) -> int:
    storage.ensure_dir(cfg.cache_dir)
    storage.save_history(cfg.cache_dir, {"shown": [], "epoch": 0})
    print("rotation history reset")
    if reset_all:
        storage.save_stories(cfg.cache_dir, [])
        print("story cache cleared")
    return 0


def _cmd_uninstall(cfg) -> int:
    changed = integration.remove(cfg)
    print("removed spinnerVerbs from settings" if changed
          else "nothing to remove (settings already clean)")

    if os.path.isdir(cfg.cache_dir):
        shutil.rmtree(cfg.cache_dir)
        print(f"removed cache: {cfg.cache_dir}")

    # Pre-plugin install leftovers: only the launcher symlink that points into
    # the old clone, never some other `spindle` on PATH.
    if (os.path.islink(_LEGACY_LAUNCHER)
            and os.path.realpath(_LEGACY_LAUNCHER).startswith(
                os.path.realpath(_LEGACY_CLONE) + os.sep)):
        os.unlink(_LEGACY_LAUNCHER)
        print(f"removed legacy launcher: {_LEGACY_LAUNCHER}")
    if os.path.isdir(_LEGACY_CLONE):
        shutil.rmtree(_LEGACY_CLONE)
        print(f"removed legacy checkout: {_LEGACY_CLONE}")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    # Global flags live on a parent parser so they are accepted either before
    # or after the subcommand. SUPPRESS defaults keep the "before" value from
    # being clobbered by the subparser's default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=_SUPPRESS, help="path to config.toml")
    common.add_argument("--mode", choices=["scraper", "api"], default=_SUPPRESS,
                        help="override the acquisition mode for this run")
    common.add_argument("-v", "--verbose", action="store_true", default=_SUPPRESS,
                        help="log progress to stderr")

    parser = argparse.ArgumentParser(
        prog="spindle", parents=[common],
        description="Developer news in the Claude Code thinking spinner.",
    )
    sub = parser.add_subparsers(dest="cmd")
    p_refresh = sub.add_parser("refresh", parents=[common], help="fetch + rebuild the cache")
    p_refresh.add_argument("--sync", action="store_true", default=_SUPPRESS,
                           help="write the refreshed pool to settings right away")
    p_refresh.add_argument("--background", action="store_true", default=_SUPPRESS,
                           help="run detached (with --sync) and return immediately")
    p_clean = sub.add_parser("clean", parents=[common],
                             help="clean headlines still waiting for the agent")
    p_clean.add_argument("--sync", action="store_true", default=_SUPPRESS,
                         help="write the pool to settings when done")
    sub.add_parser("sync", parents=[common], help="advance the pool and write settings")
    sub.add_parser("session-start", parents=[common], help="SessionStart hook entry point")
    sub.add_parser("status", parents=[common], help="show cache & integration status")
    p_reset = sub.add_parser("reset", parents=[common], help="reset rotation history")
    p_reset.add_argument("--all", action="store_true", default=_SUPPRESS,
                         help="also clear the story cache")
    sub.add_parser("uninstall", parents=[common],
                   help="remove spinnerVerbs, the cache, and pre-plugin leftovers")
    sub.add_parser("version", parents=[common], help="print version")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    verbose = getattr(args, "verbose", False)
    config_path = getattr(args, "config", None)
    mode = getattr(args, "mode", None)

    log = _make_logger(verbose)
    cfg = config_mod.load(config_path)
    if mode:
        cfg.mode = mode

    cmd = args.cmd or "status"
    try:
        if cmd == "version":
            print(f"spindle {__version__}")
            return 0
        if cmd == "refresh" and getattr(args, "background", False):
            if pipeline.spawn_refresh(cfg, _reinvoke_argv(config_path), log):
                print("spindle: refreshing in the background — the spinner "
                      "updates on its own when it's done")
            else:
                print("spindle: a refresh is already running — the spinner "
                      "updates on its own when it's done")
            return 0
        if cmd == "refresh":
            res = pipeline.refresh(cfg, log)
            if getattr(args, "sync", False) and not res.get("skipped"):
                pipeline.sync(cfg, log)
            return 0
        if cmd == "clean":
            res = pipeline.clean(cfg, log)
            if getattr(args, "sync", False) and not res.get("skipped"):
                pipeline.sync(cfg, log)
            return 0
        if cmd == "sync":
            pipeline.sync(cfg, log)
            return 0
        if cmd == "session-start":
            # Must be fast and silent: stdout from a SessionStart hook is fed to
            # Claude as context, so we print nothing there.
            pipeline.session_start(cfg, _reinvoke_argv(config_path), log)
            return 0
        if cmd == "status":
            return _cmd_status(cfg)
        if cmd == "reset":
            return _cmd_reset(cfg, getattr(args, "all", False))
        if cmd == "uninstall":
            return _cmd_uninstall(cfg)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        # The hook path must never fail loudly into Claude; other commands may
        # surface the error for debugging.
        if cmd == "session-start":
            return 0
        print(f"spindle: error: {e}", file=sys.stderr)
        return 1

    _build_parser().print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
