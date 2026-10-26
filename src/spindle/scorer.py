"""Topic filter and weighted ranking.

Off-topic stories from the general news APIs are dropped outright. Those APIs
search all of the web's news, so a title matching no developer/AI vocabulary —
and not marked a release by its feed — is noise like film news matched on a
stray word. Release *wording* ("released", "launches") doesn't count — films
launch too. Stories from developer communities (Hacker News, DEV.to, Reddit,
dev RSS feeds) skip this check: a real dev story there often has a title no
keyword list anticipates ("Recreational programming", "Math 2.0"). The headline
agent judges every story and tags the actual noise ``OFF``.

The rest are ranked. Final score = weighted sum of normalized components:

    freshness      exponential decay on age (half-life ~18h)
    signal         log-scaled popularity (HN / Lobsters points, Reddit score)
    dev_relevance  keyword hits against mobile + programming vocabulary
    ai_relevance   keyword hits against AI/LLM vocabulary
    release        bonus for SDK/framework release & changelog posts

The weighted sum is then scaled by a per-source multiplier (``source_weights``),
so a preferred source such as DEV.to can lead the ranking while others remain.
All knobs come from config so the ranking is fully tunable.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, List, Set, Tuple

from .config import Config
from .model import Story

# Vocabulary — mobile app development leads, general programming follows.
_DEV_KEYWORDS = {
    # mobile platforms & frameworks
    "ios", "android", "swift", "swiftui", "kotlin", "flutter", "dart",
    "react native", "expo", "jetpack", "compose", "xcode", "gradle",
    "app store", "play store", "testflight", "kmp", "objective-c",
    "mobile", "app clip", "widget", "apk", "aab", "ipa",
    # general programming
    "python", "javascript", "typescript", "java", "rust", "golang", "node",
    "programming", "framework", "sdk", "api", "compiler", "runtime",
    "open source", "library", "cloud", "database", "devops",
    "kubernetes", "docker", "postgres", "webassembly",
    # web platform, tooling, infra & security — so genuinely-technical stories
    # (which the narrower list missed) register as developer-relevant.
    "html", "css", "web", "browser", "http", "https", "graphql", "rest api",
    "git", "github", "gitlab", "codeberg", "cli", "shell", "terminal",
    "linux", "kernel", "unix", "sqlite", "redis", "protocol",
    "security", "vulnerability", "encryption", "authentication", "oauth",
    "typescript", "react", "vue", "svelte", "webkit", "chromium",
    "show hn", "launch hn",
    # hardware
    "cpu", "gpu", "chip", "chips", "nvidia", "intel", "amd", "semiconductor",
}

_AI_KEYWORDS = {
    "ai", "llm", "llms", "gpt", "claude", "gemini", "anthropic", "openai",
    "agentic", "ml", "machine learning",
    "transformer", "inference", "rag", "embedding", "fine-tune", "prompt",
    "mcp", "on-device", "coreml", "mlx", "diffusion", "neural",
}

_RELEASE_RE = re.compile(
    r"\b(v?\d+\.\d+(\.\d+)?|released?|releasing|launch(?:es|ed)?|"
    r"now available|generally available|\bga\b|changelog|ships?|shipped)\b",
    re.IGNORECASE,
)

# Sources whose results come from all news, not a developer community.
GENERAL_NEWS_SOURCES = {"newsapi", "gnews"}

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-\+\.]*")
_FRESHNESS_HALFLIFE_H = 18.0
_SIGNAL_REFERENCE = math.log1p(3000.0)  # ~a very strong HN/Reddit post


def _relevance(words: Set[str], text: str, keywords: Iterable[str]) -> float:
    hits = 0
    for kw in keywords:
        if " " in kw:               # phrase → substring match
            if kw in text:
                hits += 1
        elif kw in words:           # single token → whole-word match
            hits += 1
    if hits == 0:
        return 0.0
    return min(1.0, 0.6 + 0.13 * (hits - 1))


class Scorer:
    def __init__(self, cfg: Config) -> None:
        self.w = cfg.weights
        self.source_weights = cfg.source_weights or {}
        # Fold configured topics into the dev vocabulary so custom topics count.
        self.dev = set(_DEV_KEYWORDS)
        for topic in cfg.topics:
            t = topic.lower()
            if t not in ("ai", "llms", "llm"):
                self.dev.add(t)
        self.ai = set(_AI_KEYWORDS)

    def _relevance(self, s: Story) -> Tuple[float, float]:
        """``(dev, ai)`` keyword relevance of the title."""
        text = s.title.lower()
        words = set(_WORD_RE.findall(text))
        return _relevance(words, text, self.dev), _relevance(words, text, self.ai)

    def on_topic(self, s: Story) -> bool:
        if s.source not in GENERAL_NEWS_SOURCES:
            return True
        dev, ai = self._relevance(s)
        return dev > 0.0 or ai > 0.0 or s.kind == "release"

    def score(self, s: Story, now: int) -> float:
        ref_ts = s.published_ts or s.fetched_ts or now
        age_h = max(0.0, (now - ref_ts) / 3600.0)
        freshness = math.exp(-age_h / _FRESHNESS_HALFLIFE_H)

        signal = min(1.0, math.log1p(max(0.0, s.signal)) / _SIGNAL_REFERENCE)

        dev, ai = self._relevance(s)
        release = 1.0 if (s.kind == "release" or _RELEASE_RE.search(s.title)) else 0.0

        base = (
            self.w.get("freshness", 1.0) * freshness
            + self.w.get("signal", 1.0) * signal
            + self.w.get("dev_relevance", 0.8) * dev
            + self.w.get("ai_relevance", 0.6) * ai
            + self.w.get("release", 0.6) * release
        )
        # Per-source multiplier lets a preferred source (e.g. DEV.to) lead the
        # ranking without silencing the others.
        return base * float(self.source_weights.get(s.source, 1.0))


def drop_off_topic(stories: List[Story], cfg: Config) -> List[Story]:
    """The stories that are on topic (see the module docstring)."""
    scorer = Scorer(cfg)
    return [s for s in stories if scorer.on_topic(s)]


def score_all(stories, cfg: Config, now: int) -> None:
    """Assign ``.score`` to every story in place."""
    scorer = Scorer(cfg)
    for s in stories:
        s.score = scorer.score(s, now)
