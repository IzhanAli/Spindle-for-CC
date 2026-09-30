"""Headline cleaning — every spinner label is written by the headline agent.

The plugin's ``headline-writer`` agent (``agents/headline-writer.md``) rewrites
verbose feed headlines into a few tight words that fit the spinner without
truncating mid-title. It runs through the user's own Claude Code install — a
headless ``claude -p`` call using their existing login, so there is no API key
to manage.

This runs **only during ``refresh``** (always detached from the user), in
batched calls that run in parallel, and the result is cached on
``Story.ai_headline`` — so a given story is cleaned once and never again, and
render time stays free of any AI cost.

The child session is locked down and kept fast: no tools, no MCP servers, no
hooks (so it can't re-enter spindle's own SessionStart hook), no extended
thinking (rewriting a headline doesn't need it, and it made each call ~20x
slower), and no saved transcript.
Headlines are untrusted feed text; with no tools, the worst a hostile one can
do is produce an odd label, which is then sanitized and width-capped.

Cleaning is mandatory: a story without an ``ai_headline`` is never shown
(see ``pipeline.sync``). Failures here — ``claude`` not found, not logged in,
offline, a timeout, a malformed reply — are retried once per batch and
otherwise just leave those stories waiting for the next refresh. They never
let a raw feed title through to the spinner.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Config
from .model import Story
from .util import sanitize_text

Logger = Callable[[str], None]

# agents/headline-writer.md at the plugin root — the single source of truth for
# the agent's prompt, shared with in-session use as `spindle:headline-writer`.
AGENT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "agents", "headline-writer.md",
)

# Set in the child's environment; `spindle session-start` exits immediately
# when it sees it, even if hooks somehow run in the child session.
CHILD_ENV = "SPINDLE_CHILD"

# Headless sessions allowed at once; each is a full `claude` process.
MAX_PARALLEL_CALLS = 3

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)


def summarize_stories(stories: List[Story], cfg: Config, log: Logger = lambda _m: None) -> int:
    """Fill in ``ai_headline`` for every story that doesn't have one yet.

    Pending stories go to the agent in batches of ``[ai].batch_size``, several
    batches at once; a batch that fails is retried once. Returns the number of
    headlines newly cleaned.
    """
    ai = cfg.ai or {}
    pending = [s for s in stories if not (s.ai_headline or "").strip()]
    if not pending:
        return 0

    claude = find_claude(ai)
    if not claude:
        log("ai: `claude` not found on PATH (set [ai].claude_bin) — skipping")
        return 0

    agent = load_agent()
    if agent is None:
        log(f"ai: agent definition missing ({AGENT_FILE}) — skipping")
        return 0

    batch_size = max(1, int(ai.get("batch_size", 20)))
    batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]

    def clean(batch: List[Story]) -> List[str]:
        titles = [s.title for s in batch]
        return (_request_labels(titles, claude, agent, ai, cfg, log)
                or _request_labels(titles, claude, agent, ai, cfg, log))

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_CALLS, len(batches))) as pool:
        results = list(pool.map(clean, batches))

    n = 0
    for batch, labels in zip(batches, results):
        for story, label in zip(batch, labels):
            clean = sanitize_text(label)
            if clean:
                story.ai_headline = clean
                n += 1
    left = len(pending) - n
    log(f"ai: cleaned {n}/{len(pending)} headlines via {agent[0]} ({_model(ai, agent)})"
        + (f"; {left} held back until the next refresh" if left else ""))
    return n


def find_claude(ai: Dict[str, Any]) -> Optional[str]:
    """Locate the Claude Code executable: config → PATH → the running binary."""
    explicit = str(ai.get("claude_bin") or "").strip()
    if explicit:
        path = shutil.which(os.path.expanduser(explicit))
        return path or None
    return shutil.which("claude") or _executable(os.environ.get("CLAUDE_CODE_EXECPATH"))


def _executable(path: Optional[str]) -> Optional[str]:
    return path if path and os.path.isfile(path) and os.access(path, os.X_OK) else None


def load_agent(path: str = AGENT_FILE) -> Optional[Tuple[str, Dict[str, str], str]]:
    """Parse the agent file into ``(name, frontmatter, prompt)``, or None."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, sep, val = line.partition(":")
        if sep and not line.startswith((" ", "\t", "#")):
            meta[key.strip()] = val.strip().strip('"').strip("'")
    prompt = m.group(2).strip()
    name = meta.get("name") or "headline-writer"
    return (name, meta, prompt) if prompt else None


def _model(ai: Dict[str, Any], agent: Tuple[str, Dict[str, str], str]) -> str:
    return str(ai.get("model") or agent[1].get("model") or "haiku")


def _request_labels(
    titles: List[str],
    claude: str,
    agent: Tuple[str, Dict[str, str], str],
    ai: Dict[str, Any],
    cfg: Config,
    log: Logger,
) -> List[str]:
    """One batched headless-agent call. Returns labels in input order, or []."""
    name, meta, prompt = agent
    max_words = max(3, int(ai.get("max_words", 24)))
    max_chars = max(8, int(cfg.max_title_width))
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    request = (
        f"Limits: at most {max_words} words and {max_chars} characters per label. "
        f"Rewrite each of these {len(titles)} headlines.\n\n"
        + numbered
    )

    argv = [
        claude, "-p",
        "--agents", json.dumps({name: {
            "description": meta.get("description", "Rewrites headlines."),
            "prompt": prompt,
            "tools": [],
        }}),
        "--agent", name,
        "--model", _model(ai, agent),
        "--tools", "",
        "--strict-mcp-config",
        "--settings", json.dumps({"disableAllHooks": True, "alwaysThinkingEnabled": False}),
        "--no-session-persistence",
        "--output-format", "json",
    ]
    env = dict(os.environ)
    env[CHILD_ENV] = "1"

    try:
        proc = subprocess.run(
            argv,
            input=request,
            capture_output=True,
            text=True,
            encoding="utf-8",         # not the locale codepage (cp1252 on Windows)
            errors="replace",
            timeout=float(ai.get("timeout_secs", 120.0)),
            env=env,
            cwd=cfg.cache_dir,        # keep project CLAUDE.md files out of context
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"ai: claude call failed ({e!r})")
        return []

    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
        log(f"ai: claude exited {proc.returncode} ({detail[0][:200]})")
        return []
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        result = envelope.get("result") if isinstance(envelope, dict) else envelope
        log(f"ai: claude reported an error ({str(result)[:200]})")
        return []

    labels = parse_labels(str(envelope.get("result") or ""))
    if labels is None:
        log("ai: response had no 'labels' array — skipping")
        return []
    if len(labels) != len(titles):
        # A miscounted reply can't be matched back to its inputs safely.
        log(f"ai: got {len(labels)} labels for {len(titles)} headlines — skipping")
        return []
    return labels


def parse_labels(text: str) -> Optional[List[str]]:
    """Pull ``{"labels": [...]}`` out of the agent's reply.

    Models sometimes wrap JSON in a code fence or add a stray sentence despite
    instructions, so we take the outermost ``{...}`` span rather than trusting
    the reply to be bare JSON.
    """
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except ValueError:
        return None
    labels = parsed.get("labels") if isinstance(parsed, dict) else None
    if not isinstance(labels, list):
        return None
    return [str(x) for x in labels]
