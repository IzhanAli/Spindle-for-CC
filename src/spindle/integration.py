"""Claude Code integration — the *renderer* side of the project.

There is intentionally **no render-time code**. Claude Code renders the spinner
itself; we only supply the word list via the officially supported settings key:

    "spinnerVerbs": { "mode": "replace", "verbs": [ "AI • …", "IOS • …", … ] }

Claude picks one verb at random per thinking episode and composes the rest of
the line — elapsed time, token count, spinner, effort, colors, ANSI — exactly
as it always does. We replace one rendered token; nothing else is touched.

A ``SessionStart`` hook (also written here) runs ``spindle session-start`` once
per session to rotate the pool. No daemon, no timer, no polling.

Writes are atomic, pretty-printed, and *only happen when content changes*, so we
never churn the user's settings file.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .model import Story
from .storage import read_json
from .util import truncate_width

HOOK_MARKER = "spindle"          # identifies a hook entry as ours
HOOK_SUBCOMMAND = "session-start"


def build_display(story: Story, cfg: Config) -> str:
    """Compose the single-line verb from the headline alone, width-capped.

    The source tag (``prefix``) is intentionally *not* shown — the whole width
    budget goes to the headline itself, which no longer truncates just to make
    room for a "HN • " tag. When the AI layer has produced a compressed
    ``ai_headline`` we render that; otherwise we fall back to the raw title.

    No trailing ellipsis — Claude Code appends "…" after every verb, which also
    serves as the truncation indicator.
    """
    budget = max(8, int(cfg.max_title_width))
    title = (story.ai_headline or story.title).strip()
    return truncate_width(title, budget)


def build_verbs(pool: List[Story], cfg: Config) -> List[str]:
    verbs: List[str] = []
    seen: set = set()
    for story in pool:
        verb = build_display(story, cfg)
        if verb and verb not in seen:
            seen.add(verb)
            verbs.append(verb)
    return verbs


def _ensure_hook(settings: Dict[str, Any], command: str) -> bool:
    """Idempotently ensure our SessionStart hook is present & current.

    Returns True if ``settings`` was modified.
    """
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return False
    session_start = hooks.setdefault("SessionStart", [])
    if not isinstance(session_start, list):
        return False

    for group in session_start:
        for entry in group.get("hooks", []) if isinstance(group, dict) else []:
            cmd = entry.get("command", "") if isinstance(entry, dict) else ""
            if HOOK_MARKER in cmd and HOOK_SUBCOMMAND in cmd:
                if cmd == command:
                    return False            # already correct
                entry["command"] = command  # path changed → update in place
                return True

    session_start.append({
        "hooks": [{"type": "command", "command": command, "timeout": 10}]
    })
    return True


def _atomic_write_pretty(path: str, obj: Any) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def apply_pool(
    cfg: Config,
    pool: List[Story],
    hook_command: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """Write ``spinnerVerbs`` (and, if enabled, the SessionStart hook) into the
    Claude Code settings file, merging non-destructively.

    Returns ``(changed, verbs)``. Existing keys are preserved; the file is only
    rewritten when something actually changed.
    """
    path = cfg.claude_settings_path
    settings = read_json(path, {})
    if not isinstance(settings, dict):
        settings = {}

    verbs = build_verbs(pool, cfg)
    changed = False

    if verbs:                              # never write an empty replace list
        new_spinner = {"mode": "replace", "verbs": verbs}
        if settings.get("spinnerVerbs") != new_spinner:
            settings["spinnerVerbs"] = new_spinner
            changed = True

    if cfg.manage_hook and hook_command:
        if _ensure_hook(settings, hook_command):
            changed = True

    if changed:
        _atomic_write_pretty(path, settings)

    return changed, verbs


def current_verbs(cfg: Config) -> List[str]:
    """Read back the verbs currently installed (for `status`)."""
    settings = read_json(cfg.claude_settings_path, {})
    sv = settings.get("spinnerVerbs") if isinstance(settings, dict) else None
    if isinstance(sv, dict) and isinstance(sv.get("verbs"), list):
        return [v for v in sv["verbs"] if isinstance(v, str)]
    return []


def remove(cfg: Config) -> bool:
    """Remove our ``spinnerVerbs`` and SessionStart hook from settings.

    Leaves every other key (and other people's hooks) untouched. Returns True
    if anything was removed.
    """
    path = cfg.claude_settings_path
    settings = read_json(path, {})
    if not isinstance(settings, dict):
        return False
    changed = False

    if "spinnerVerbs" in settings:
        del settings["spinnerVerbs"]
        changed = True

    hooks = settings.get("hooks")
    if isinstance(hooks, dict) and isinstance(hooks.get("SessionStart"), list):
        groups = []
        for group in hooks["SessionStart"]:
            entries = group.get("hooks", []) if isinstance(group, dict) else []
            kept = [
                e for e in entries
                if not (isinstance(e, dict)
                        and HOOK_MARKER in e.get("command", "")
                        and HOOK_SUBCOMMAND in e.get("command", ""))
            ]
            if len(kept) != len(entries):
                changed = True
            if kept:
                group["hooks"] = kept
                groups.append(group)
        if groups:
            hooks["SessionStart"] = groups
        else:
            del hooks["SessionStart"]
        if not hooks:
            del settings["hooks"]

    if changed:
        _atomic_write_pretty(path, settings)
    return changed


def hook_installed(cfg: Config) -> bool:
    settings = read_json(cfg.claude_settings_path, {})
    if not isinstance(settings, dict):
        return False
    for group in settings.get("hooks", {}).get("SessionStart", []) or []:
        for entry in group.get("hooks", []) if isinstance(group, dict) else []:
            cmd = entry.get("command", "") if isinstance(entry, dict) else ""
            if HOOK_MARKER in cmd and HOOK_SUBCOMMAND in cmd:
                return True
    return False
