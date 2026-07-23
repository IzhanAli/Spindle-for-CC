"""Weighted ranking.

Final score = weighted sum of normalized components:

    freshness      exponential decay on age (half-life ~18h)
    signal         log-scaled popularity (HN points / Reddit score)
    dev_relevance  keyword hits against mobile + programming vocabulary
    ai_relevance   keyword hits against AI/LLM vocabulary
    release        bonus for SDK/framework release & changelog posts

The weighted sum is then scaled by a per-source multiplier (``source_weights``),
so a preferred source such as DEV.to can lead the ranking while others remain.
Finally, stories that match no developer/AI vocabulary and aren't a release are
scaled down by ``offtopic_penalty`` — this is what keeps general-interest Hacker
News front-page items out of a *developer* news pool. All knobs come from config
so the ranking is fully tunable.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, Set

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
    "python", "javascript", "typescript", "java", "rust", "go", "node",
    "programming", "framework", "sdk", "api", "compiler", "runtime",
    "open source", "library", "release", "cloud", "database", "devops",
    "kubernetes", "docker", "postgres", "webassembly", "performance",
    # web platform, tooling, infra & security — so genuinely-technical stories
    # (which the narrower list missed) register as developer-relevant.
    "html", "css", "web", "browser", "http", "https", "graphql", "rest",
    "git", "github", "gitlab", "codeberg", "cli", "shell", "terminal",
    "linux", "kernel", "unix", "sqlite", "redis", "protocol",
    "security", "vulnerability", "encryption", "authentication", "oauth",
    "typescript", "react", "vue", "svelte", "webkit", "chromium",
}

_AI_KEYWORDS = {
    "ai", "llm", "llms", "gpt", "claude", "gemini", "anthropic", "openai",
    "model", "models", "agent", "agents", "ml", "machine learning",
    "transformer", "inference", "rag", "embedding", "fine-tune", "prompt",
    "mcp", "on-device", "coreml", "mlx", "diffusion", "neural",
}

_RELEASE_RE = re.compile(
    r"\b(v?\d+\.\d+(\.\d+)?|released?|releasing|launch(?:es|ed)?|"
    r"now available|generally available|\bga\b|changelog|ships?|shipped)\b",
    re.IGNORECASE,
)

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
        self.offtopic_penalty = float(getattr(cfg, "offtopic_penalty", 0.35))
        # Fold configured topics into the dev vocabulary so custom topics count.
        self.dev = set(_DEV_KEYWORDS)
        for topic in cfg.topics:
            t = topic.lower()
            if t not in ("ai", "llms", "llm"):
                self.dev.add(t)
        self.ai = set(_AI_KEYWORDS)

    def score(self, s: Story, now: int) -> float:
        ref_ts = s.published_ts or s.fetched_ts or now
        age_h = max(0.0, (now - ref_ts) / 3600.0)
        freshness = math.exp(-age_h / _FRESHNESS_HALFLIFE_H)

        signal = min(1.0, math.log1p(max(0.0, s.signal)) / _SIGNAL_REFERENCE)

        text = s.title.lower()
        words = set(_WORD_RE.findall(text))
        dev = _relevance(words, text, self.dev)
        ai = _relevance(words, text, self.ai)
        release = 1.0 if (s.kind == "release" or _RELEASE_RE.search(text)) else 0.0

        base = (
            self.w.get("freshness", 1.0) * freshness
            + self.w.get("signal", 1.0) * signal
            + self.w.get("dev_relevance", 0.8) * dev
            + self.w.get("ai_relevance", 0.6) * ai
            + self.w.get("release", 0.6) * release
        )
        # Per-source multiplier lets a preferred source (e.g. DEV.to) lead the
        # ranking without silencing the others.
        score = base * float(self.source_weights.get(s.source, 1.0))

        # Off-topic penalty: a story that matches no dev/AI vocabulary and isn't
        # a release is general-interest noise (e.g. front-page HN chatter) in a
        # *developer* news tool. Demote it so freshness + popularity alone can't
        # float it into the pool ahead of relevant stories.
        if dev == 0.0 and ai == 0.0 and release == 0.0:
            score *= self.offtopic_penalty
        return score


def score_all(stories, cfg: Config, now: int) -> None:
    """Assign ``.score`` to every story in place."""
    scorer = Scorer(cfg)
    for s in stories:
        s.score = scorer.score(s, now)
