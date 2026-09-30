"""Claude Code integration — the *renderer* side of the project.

There is intentionally **no render-time code**. Claude Code renders the spinner
itself; we only supply the word list via the officially supported settings key:

    "spinnerVerbs": { "mode": "replace", "verbs": [ "AI • …", "IOS • …", … ] }

Claude picks one verb at random per thinking episode and composes the rest of
the line — elapsed time, token count, spinner, effort, colors, ANSI — exactly
as it always does. We replace one rendered token; nothing else is touched.

The plugin's own ``SessionStart`` hook (``hooks/hooks.json``) runs
``spindle session-start`` once per session to rotate the pool. No daemon, no
timer, no polling. Plugins can't contribute ``spinnerVerbs`` themselves, so this
one key is written to the user settings file.

Before the plugin, the curl installer wrote its own SessionStart hook into
settings. That legacy entry is removed on every sync so the pool doesn't rotate
twice per session. (The old install re-adds it if its own hook runs after ours
in the same session start, so running its ``spindle uninstall`` is the clean
upgrade path; this is the fallback.)

Writes are atomic, pretty-printed, and *only happen when content changes*, so we
never churn the user's settings file.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Tuple

from .config import Config
from .model import Story
from .storage import read_json
from .util import truncate_width

HOOK_MARKER = "spindle"          # identifies a legacy hook entry as ours
HOOK_SUBCOMMAND = "session-start"


def build_display(story: Story, cfg: Config) -> str:
    """Compose the single-line verb from the cleaned headline, width-capped.

    The source tag (``prefix``) is intentionally *not* shown — the whole width
    budget goes to the headline itself. Only the agent-cleaned ``ai_headline``
    is ever rendered; a story without one yields "" and is skipped, so a raw
    feed title can never reach the spinner. The agent is asked to fit the
    width budget, so the cap here is only a safety net.

    No trailing ellipsis — Claude Code appends "…" after every verb, which also
    serves as the truncation indicator.
    """
    budget = max(8, int(cfg.max_title_width))
    return truncate_width((story.ai_headline or "").strip(), budget)


def build_verbs(pool: List[Story], cfg: Config) -> List[str]:
    verbs: List[str] = []
    seen: set = set()
    for story in pool:
        verb = build_display(story, cfg)
        if verb and verb not in seen:
            seen.add(verb)
            verbs.append(verb)
    return verbs


def _is_legacy_hook(entry: Any) -> bool:
    cmd = entry.get("command", "") if isinstance(entry, dict) else ""
    return isinstance(cmd, str) and HOOK_MARKER in cmd and HOOK_SUBCOMMAND in cmd


def _strip_legacy_hook(settings: Dict[str, Any]) -> bool:
    """Drop SessionStart entries written by the pre-plugin installer.

    Leaves other people's hooks untouched and prunes containers we empty.
    Returns True if ``settings`` was modified.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get("SessionStart"), list):
        return False
    changed = False
    groups = []
    for group in hooks["SessionStart"]:
        entries = group.get("hooks", []) if isinstance(group, dict) else []
        kept = [e for e in entries if not _is_legacy_hook(e)]
        if len(kept) != len(entries):
            changed = True
        if kept:
            group["hooks"] = kept
            groups.append(group)
        elif not entries:
            groups.append(group)       # not ours to judge — keep as found
    if not changed:
        return False
    if groups:
        hooks["SessionStart"] = groups
    else:
        del hooks["SessionStart"]
    if not hooks:
        del settings["hooks"]
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


def apply_pool(cfg: Config, pool: List[Story]) -> Tuple[bool, List[str]]:
    """Write ``spinnerVerbs`` into the Claude Code settings file, merging
    non-destructively, and remove any legacy spindle hook.

    Returns ``(changed, verbs)``. Existing keys are preserved; the file is only
    rewritten when something actually changed.
    """
    path = cfg.claude_settings_path
    settings = read_json(path, {})
    if not isinstance(settings, dict):
        settings = {}

    verbs = build_verbs(pool, cfg)
    changed = _strip_legacy_hook(settings)

    if verbs:                              # never write an empty replace list
        new_spinner = {"mode": "replace", "verbs": verbs}
        if settings.get("spinnerVerbs") != new_spinner:
            settings["spinnerVerbs"] = new_spinner
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
    """Remove our ``spinnerVerbs`` (and any legacy hook) from settings.

    Leaves every other key (and other people's hooks) untouched. Returns True
    if anything was removed.
    """
    path = cfg.claude_settings_path
    settings = read_json(path, {})
    if not isinstance(settings, dict):
        return False

    changed = _strip_legacy_hook(settings)
    if "spinnerVerbs" in settings:
        del settings["spinnerVerbs"]
        changed = True

    if changed:
        _atomic_write_pretty(path, settings)
    return changed


def legacy_hook_installed(cfg: Config) -> bool:
    settings = read_json(cfg.claude_settings_path, {})
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks")
    groups = hooks.get("SessionStart") if isinstance(hooks, dict) else None
    for group in groups if isinstance(groups, list) else []:
        for entry in group.get("hooks", []) if isinstance(group, dict) else []:
            if _is_legacy_hook(entry):
                return True
    return False
