"""Configuration: TOML on disk, layered over built-in defaults.

The tool works out of the box with *no* config file — the defaults below make
it useful immediately. A user config (``config.toml``) is merged shallowly over
these defaults; source tables (``[hackernews]`` etc.) merge key-by-key.

Config discovery order:
    1. ``--config PATH`` / ``$SPINDLE_CONFIG``
    2. ``$SPINDLE_HOME/config.toml``
    3. ``$XDG_CONFIG_HOME/spindle/config.toml``  (defaults to ~/.config)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:                       # Python 3.11+
    import tomllib as _toml
    _TOML_LOADS = lambda b: _toml.loads(b.decode("utf-8"))  # noqa: E731
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    try:
        import tomli as _toml  # type: ignore
        _TOML_LOADS = lambda b: _toml.loads(b.decode("utf-8"))  # noqa: E731
    except ModuleNotFoundError:
        _TOML_LOADS = None  # handled in load()


# --------------------------------------------------------------------------- #
# Defaults — mobile app development first, broader dev landscape second.
# (Rust and Security intentionally excluded.)
# --------------------------------------------------------------------------- #

DEFAULT_TOPICS = [
    # Mobile app development — the primary focus.
    "Mobile", "iOS", "Android", "Swift", "SwiftUI", "Kotlin",
    "Jetpack Compose", "Flutter", "React Native", "Expo",
    "App Store", "Play Store",
    # Broader developer landscape.
    "AI", "LLMs", "Programming", "Open Source", "DevOps",
    "Python", "Java", "JavaScript", "Cloud", "Databases",
]

DEFAULT_WEIGHTS = {
    "freshness": 1.0,
    "signal": 1.0,
    "dev_relevance": 0.8,   # mobile / programming relevance
    "ai_relevance": 0.6,
    "release": 0.6,         # SDK / framework release & changelog posts
}

# Per-source score multipliers, keyed by a story's ``source`` (not prefix). A
# source not listed here is 1.0 (neutral). DEV.to is boosted so its articles
# lead the ranking while other sources still appear.
DEFAULT_SOURCE_WEIGHTS = {
    "devto": 2.0,
}

DEFAULT_HACKERNEWS = {
    "enabled": True,
    "prefix": "HN",
    "min_points": 80,
    "limit": 30,
}

# The keyed API sources search a single query string. Left empty, it is derived
# from `topics` at fetch time (priority-ordered, mobile-first, joined with OR and
# capped to each API's length limit). Set an explicit `query` to override.
DEFAULT_NEWSAPI = {
    "enabled": True,
    "prefix": "NEWS",
    "api_key": "",            # or $NEWSAPI_KEY
    "query": "",              # empty → built from `topics`
    "language": "en",
    "page_size": 30,
    "daily_limit": 100,       # free-tier cap (requests/day); we never exceed it
}

DEFAULT_GNEWS = {
    "enabled": True,
    "prefix": "GNEWS",
    "api_key": "",            # or $GNEWS_KEY
    "query": "",              # empty → built from `topics`
    "lang": "en",
    "max": 10,                # free tier caps at 10 articles/request
    "daily_limit": 90,        # free-tier cap (requests/day); we never exceed it
}

DEFAULT_DEVTO = {
    "enabled": True,
    "prefix": "DEV",
    "min_reactions": 10,
    "per_page": 15,
    "top_days": 7,            # DEV.to "top of the last N days"
    "tags": ["android", "ios", "flutter", "reactnative",
             "kotlin", "swift", "ai", "programming"],
}

# Optional AI layer: a cheap chat model that compresses raw headlines into short
# spinner labels. Runs only during `refresh`, one batched request. Active only
# when a key is present (`api_key` here or $OPENAI_API_KEY) — no key means a
# complete no-op, so the tool still works with zero configuration and zero cost.
DEFAULT_AI = {
    "enabled": True,
    "api_key": "",            # or $OPENAI_API_KEY (e.g. from a .env file)
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",   # any cheap chat model (gpt-4o-mini, gpt-4.1-nano, …)
    "max_words": 24,          # target length of each compressed headline
    "max_items": 40,          # cap headlines summarized per refresh (cost guard)
    "timeout_secs": 20.0,
}

DEFAULT_REDDIT = {
    "enabled": True,
    "prefix": "RDT",
    "min_score": 100,
    "limit": 20,
    "subreddits": [
        # Mobile-focused.
        "androiddev", "iOSProgramming", "reactnative", "FlutterDev",
        "Kotlin", "swift",
        # Broader.
        "programming", "javascript", "MachineLearning", "LocalLLaMA",
    ],
}

# A curated, reasonably stable default set of RSS/Atom feeds. Each carries the
# short display prefix used in the status line. Dead/renamed feeds are simply
# skipped at fetch time — the renderer never depends on any single one.
DEFAULT_RSS: List[Dict[str, Any]] = [
    # --- Mobile app development ---
    {"prefix": "AND",    "url": "https://android-developers.googleblog.com/feeds/posts/default"},
    {"prefix": "IOS",    "url": "https://developer.apple.com/news/rss/news.rss"},
    {"prefix": "SWIFT",  "url": "https://www.swift.org/atom.xml"},
    {"prefix": "KOTLIN", "url": "https://blog.jetbrains.com/kotlin/feed/"},
    {"prefix": "RN",     "url": "https://reactnative.dev/blog/rss.xml"},
    {"prefix": "FLUTTER","url": "https://medium.com/feed/flutter"},
    {"prefix": "EXPO",   "url": "https://expo.dev/changelog/rss.xml"},
    # --- Broader developer landscape ---
    {"prefix": "AI",     "url": "https://simonwillison.net/atom/everything/"},
    {"prefix": "AI",     "url": "https://huggingface.co/blog/feed.xml"},
    {"prefix": "AI",     "url": "https://openai.com/news/rss.xml"},
    {"prefix": "PY",     "url": "https://realpython.com/atom.xml"},
    {"prefix": "DEV",    "url": "https://martinfowler.com/feed.atom"},
    {"prefix": "DEVOPS", "url": "https://kubernetes.io/feed.xml"},
    {"prefix": "CLOUD",  "url": "https://aws.amazon.com/about-aws/whats-new/recent/feed/"},
    {"prefix": "DB",     "url": "https://www.postgresql.org/news.rss"},
]


@dataclass
class Config:
    # Acquisition layer: "scraper" (RSS + Hacker News + Reddit) or
    # "api" (NewsAPI + GNews + DEV.to + Hacker News). Mutually exclusive.
    mode: str = "scraper"

    # ranking: score multiplier for off-topic stories (no dev/AI keyword match
    # and not a release). Lower = harsher demotion of general-interest noise.
    offtopic_penalty: float = 0.35

    # cache / rendering knobs
    ttl_minutes: int = 30
    max_stories: int = 60
    max_age_hours: int = 72        # stories older than this are dropped from cache
    history_size: int = 800
    pool_size: int = 14            # how many verbs are placed for a session
    max_title_width: int = 56      # display columns budget for the whole verb
    display_separator: str = " • "

    # networking
    request_timeout_secs: float = 8.0
    max_feed_bytes: int = 5_000_000
    user_agent: str = "spindle/0.1 (+https://github.com/izhanali/spindle-claude-code)"
    # Optional TLS CA bundle path. Leave empty to auto-detect (certifi / system
    # bundle). Set this to your corporate root CA if behind a TLS-inspecting
    # proxy. Also honored via $SPINDLE_CA_BUNDLE / $SSL_CERT_FILE.
    ca_bundle: str = ""

    # locations
    cache_dir: str = ""            # resolved in __post_init__
    claude_settings_path: str = "~/.claude/settings.json"
    manage_hook: bool = True

    topics: List[str] = field(default_factory=lambda: list(DEFAULT_TOPICS))
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    source_weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SOURCE_WEIGHTS))
    # scraper-mode sources
    hackernews: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_HACKERNEWS))
    reddit: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_REDDIT))
    rss: List[Dict[str, Any]] = field(default_factory=lambda: [dict(x) for x in DEFAULT_RSS])
    # api-mode sources
    newsapi: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_NEWSAPI))
    gnews: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_GNEWS))
    devto: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_DEVTO))
    # optional AI headline summarizer (both modes)
    ai: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_AI))

    def __post_init__(self) -> None:
        self.claude_settings_path = os.path.expanduser(self.claude_settings_path)
        if not self.cache_dir:
            base = os.environ.get(
                "XDG_CACHE_HOME", os.path.expanduser("~/.cache")
            )
            self.cache_dir = os.path.join(base, "spindle")
        self.cache_dir = os.path.expanduser(self.cache_dir)

    @property
    def ttl_seconds(self) -> int:
        return int(self.ttl_minutes * 60)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _config_search_paths(explicit: Optional[str]) -> List[str]:
    paths: List[str] = []
    if explicit:
        paths.append(explicit)
    env = os.environ.get("SPINDLE_CONFIG")
    if env:
        paths.append(env)
    home = os.environ.get("SPINDLE_HOME")
    if home:
        paths.append(os.path.join(home, "config.toml"))
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    paths.append(os.path.join(xdg, "spindle", "config.toml"))
    return [os.path.expanduser(p) for p in paths]


def _merge_table(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    out.update(override or {})
    return out


def _load_dotenv() -> None:
    """Best-effort: load ``KEY=VALUE`` lines from a ``.env`` file into the
    environment, so secrets like ``OPENAI_API_KEY`` can live in a file instead
    of the shell profile.

    Precedence is standard dotenv: an already-exported variable always wins, so
    the file never overrides the real environment. Never raises. The first
    existing file in this search order is used:

        1. ``$SPINDLE_ENV``
        2. ``$SPINDLE_HOME/.env``
        3. ``$XDG_CONFIG_HOME/spindle/.env``   (next to config.toml)
        4. ``./.env``                          (current working directory)
    """
    candidates: List[str] = []
    if os.environ.get("SPINDLE_ENV"):
        candidates.append(os.environ["SPINDLE_ENV"])
    if os.environ.get("SPINDLE_HOME"):
        candidates.append(os.path.join(os.environ["SPINDLE_HOME"], ".env"))
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    candidates.append(os.path.join(xdg, "spindle", ".env"))
    candidates.append(os.path.join(os.getcwd(), ".env"))

    for path in candidates:
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key.startswith("export "):
                        key = key[len("export "):].strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
        except OSError:
            continue
        break  # first existing file wins


def load(explicit_path: Optional[str] = None) -> Config:
    """Load config, layering any found TOML file over the defaults."""
    _load_dotenv()
    cfg = Config()
    path = next((p for p in _config_search_paths(explicit_path) if os.path.isfile(p)), None)
    if not path:
        return cfg
    if _TOML_LOADS is None:  # pragma: no cover
        raise RuntimeError(
            "A config file was found but no TOML parser is available. "
            "Use Python 3.11+ or `pip install tomli`."
        )
    with open(path, "rb") as fh:
        data = _TOML_LOADS(fh.read())

    scalars = (
        "mode", "offtopic_penalty",
        "ttl_minutes", "max_stories", "max_age_hours", "history_size",
        "pool_size", "max_title_width", "display_separator",
        "request_timeout_secs", "max_feed_bytes", "user_agent", "ca_bundle",
        "cache_dir", "claude_settings_path", "manage_hook",
    )
    for key in scalars:
        if key in data:
            setattr(cfg, key, data[key])

    if "topics" in data:
        cfg.topics = list(data["topics"])
    if "weights" in data:
        cfg.weights = _merge_table(DEFAULT_WEIGHTS, data["weights"])
    if "source_weights" in data:
        cfg.source_weights = _merge_table(DEFAULT_SOURCE_WEIGHTS, data["source_weights"])
    if "hackernews" in data:
        cfg.hackernews = _merge_table(DEFAULT_HACKERNEWS, data["hackernews"])
    if "reddit" in data:
        cfg.reddit = _merge_table(DEFAULT_REDDIT, data["reddit"])
    if "rss" in data:                 # array-of-tables replaces the default list
        cfg.rss = [dict(x) for x in data["rss"]]
    if "newsapi" in data:
        cfg.newsapi = _merge_table(DEFAULT_NEWSAPI, data["newsapi"])
    if "gnews" in data:
        cfg.gnews = _merge_table(DEFAULT_GNEWS, data["gnews"])
    if "devto" in data:
        cfg.devto = _merge_table(DEFAULT_DEVTO, data["devto"])
    if "ai" in data:
        cfg.ai = _merge_table(DEFAULT_AI, data["ai"])

    # Re-resolve derived paths after overrides.
    cfg.__post_init__()
    return cfg
