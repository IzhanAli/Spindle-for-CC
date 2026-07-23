"""Collapse duplicate stories, keeping the best-scored representative.

Two passes:
  1. Exact: identical normalized URL.
  2. Near-duplicate: high title similarity (same story syndicated across feeds).

Input is expected to be already scored; we process highest-score-first so the
survivor of any collision is the best version. A token inverted index keeps the
near-duplicate pass close to linear in practice despite the pairwise check.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from .model import Story
from .util import title_similarity, title_tokens

# Tuned so genuine syndication (near-identical titles, ~0.8–1.0) merges while
# version-only variants ("RN 0.79" vs "RN 0.80", ~0.6) stay distinct.
SIM_THRESHOLD = 0.65


def deduplicate(stories: List[Story], sim_threshold: float = SIM_THRESHOLD) -> List[Story]:
    ordered = sorted(stories, key=lambda s: s.score, reverse=True)

    kept: List[Story] = []
    seen_url: set = set()
    token_index: Dict[str, List[int]] = defaultdict(list)

    for s in ordered:
        if s.norm_url and s.norm_url in seen_url:
            continue

        tokens = title_tokens(s.title)
        candidates = set()
        for tok in tokens:
            candidates.update(token_index.get(tok, ()))

        if any(title_similarity(s.title, kept[i].title) >= sim_threshold
               for i in candidates):
            continue

        idx = len(kept)
        kept.append(s)
        if s.norm_url:
            seen_url.add(s.norm_url)
        for tok in tokens:
            token_index[tok].append(idx)

    return kept
