"""Command-line interface.

    spindle install         seed the cache and register the SessionStart hook
    spindle refresh         fetch + rebuild the cache (network)
    spindle sync            advance the pool and write settings (local)
    spindle session-start   SessionStart hook entry point (sync + maybe refresh)
    spindle status          show cache & integration status
    spindle reset [--all]   reset rotation history (and optionally the cache)
    spindle uninstall       remove spinnerVerbs + hook from settings
    spindle version

Global flags (--config, --mode, -v) work before or after the subcommand.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from typing import List, Optional
import shutil
from pathlib import Path

from . import __version__
from . import config as config_mod
from . import integration, pipeline, storage

_SUPPRESS = argparse.SUPPRESS


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


def _hook_command(explicit_config: Optional[str]) -> str:
    argv = _reinvoke_argv(explicit_config) + ["session-start"]
    return " ".join(shlex.quote(a) for a in argv)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _cmd_install(cfg, no_refresh, config_path, hook_command, log) -> int:
    storage.ensure_dir(cfg.cache_dir)
    print(f"spindle: installing (mode={cfg.mode})…")
    if not no_refresh:
        print("  • fetching initial news (one-time, network)…")
        res = pipeline.refresh(cfg, log)
        print(f"    cached {res.get('cached', 0)} stories")
    res = pipeline.sync(cfg, hook_command, log)
    print(f"  • wrote {res['pool']} spinner verbs to {cfg.claude_settings_path}")
    print(f"  • SessionStart hook: {'installed' if integration.hook_installed(cfg) else 'NOT installed'}")
    print()
    print("Done. New Claude Code sessions will show developer news in the spinner.")
    print("Keep the cache warm with a periodic refresh, e.g. add to crontab:")
    argv = _reinvoke_argv(config_path) + ["refresh"]
    print(f"    */30 * * * * {' '.join(shlex.quote(a) for a in argv)}")
    return 0


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
    print(f"stories cached  {info['story_count']}")
    print(f"last refresh    {_fmt_age(info['age_secs'])}"
          f"  ({'STALE' if info['stale'] else 'fresh'})")
    print(f"rotation        {info['history_shown']} shown, epoch {info['history_epoch']}")
    print(f"hook installed  {'yes' if info['hook_installed'] else 'no'}")
    verbs = info["installed_verbs"]
    print(f"verbs in pool   {len(verbs)}")
    for v in verbs[:8]:
        print(f"    {v}…")
    if info["top"]:
        print("top stories:  (ai = AI-summarized headline)")
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
    print("removed spinnerVerbs + hook from settings" if changed
          else "nothing to remove (settings already clean)")

    cache_dir = Path(cfg.cache_dir).expanduser()
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        print(f"removed cache: {cache_dir}")

    clone_dir = Path.home() / ".local" / "share" / "spindle-claude-code"
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
        print(f"removed installed bin: {clone_dir}")
        print("(reinstall via curl from README, not `spindle install`)")

    launcher = Path.home() / ".local" / "bin" / "spindle"
    if launcher.exists() or launcher.is_symlink():
        launcher.unlink()
        print(f"Uninstalled Spindle!")

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
    sub.add_parser("refresh", parents=[common], help="fetch + rebuild the cache")
    sub.add_parser("sync", parents=[common], help="advance the pool and write settings")
    sub.add_parser("session-start", parents=[common], help="SessionStart hook entry point")
    p_install = sub.add_parser("install", parents=[common], help="seed cache + register the hook")
    p_install.add_argument("--no-refresh", action="store_true", default=_SUPPRESS,
                           help="skip the initial network fetch")
    sub.add_parser("status", parents=[common], help="show cache & integration status")
    p_reset = sub.add_parser("reset", parents=[common], help="reset rotation history")
    p_reset.add_argument("--all", action="store_true", default=_SUPPRESS,
                         help="also clear the story cache")
    sub.add_parser("uninstall", parents=[common], help="remove spinnerVerbs + hook from settings")
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
    hook_command = _hook_command(config_path) if cfg.manage_hook else None

    cmd = args.cmd or "status"
    try:
        if cmd == "version":
            print(f"spindle {__version__}")
            return 0
        if cmd == "refresh":
            pipeline.refresh(cfg, log)
            return 0
        if cmd == "sync":
            pipeline.sync(cfg, hook_command, log)
            return 0
        if cmd == "session-start":
            # Must be fast and silent: stdout from a SessionStart hook is fed to
            # Claude as context, so we print nothing there.
            refresh_argv = _reinvoke_argv(config_path) + ["refresh"]
            pipeline.session_start(cfg, hook_command, refresh_argv, log)
            return 0
        if cmd == "install":
            return _cmd_install(cfg, getattr(args, "no_refresh", False),
                                config_path, hook_command, log)
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
