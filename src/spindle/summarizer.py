"""Optional AI layer — compress raw headlines into short spinner labels.

A cheap chat model (default ``gpt-4o-mini``) rewrites verbose feed headlines
into a few tight words that fit the spinner without truncating mid-title. This
runs **only during ``refresh``**, in a single batched request per refresh, and
the result is cached on ``Story.ai_headline`` — so a given story is summarized
once and never again, and render time stays free of any AI cost.

Everything here degrades silently: no API key, offline, a rate limit, or a
malformed response all leave the original titles untouched. The spinner always
has something to show.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List

from . import http
from .config import Config
from .model import Story
from .util import sanitize_text

Logger = Callable[[str], None]


def _api_key(ai: Dict[str, Any]) -> str:
    return (str(ai.get("api_key") or "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip())


def summarize_stories(stories: List[Story], cfg: Config, log: Logger = lambda _m: None) -> int:
    """Fill in ``ai_headline`` for stories that don't have one yet.

    Returns the number of headlines newly summarized. No-ops (returning 0) when
    the layer is disabled, no key is configured, or nothing needs summarizing.
    """
    ai = cfg.ai or {}
    if not ai.get("enabled", True):
        return 0

    key = _api_key(ai)
    if not key:
        log("ai: no API key (set OPENAI_API_KEY or [ai].api_key) — skipping")
        return 0

    max_items = max(1, int(ai.get("max_items", 40)))
    pending = [s for s in stories if not (s.ai_headline or "").strip()][:max_items]
    if not pending:
        return 0

    labels = _request_labels([s.title for s in pending], ai, key, cfg, log)
    if not labels:
        return 0

    n = 0
    for story, label in zip(pending, labels):
        clean = sanitize_text(label)
        if clean:
            story.ai_headline = clean
            n += 1
    log(f"ai: summarized {n}/{len(pending)} headlines via {ai.get('model')}")
    return n


def _request_labels(
    titles: List[str],
    ai: Dict[str, Any],
    key: str,
    cfg: Config,
    log: Logger,
) -> List[str]:
    """One batched chat-completion call. Returns labels in input order, or []."""
    max_words = max(3, int(ai.get("max_words", 24)))
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))

    system = (
        "You rewrite developer-news headlines into concise, natural status-line "
        f"labels of at most {max_words} words. Follow these rules in priority order:\n"
        "1. Preserve concrete specifics verbatim — product, tool, project, company "
        "and model names, version numbers, and any named list or comparison. Keep "
        "'Petals' or 'GPT-4o, Claude, Gemini'; never collapse them to 'an LLM tool' "
        "or 'multiple AI'.\n"
        "2. Keep the headline's real hook — if it asks a question or makes a specific "
        "claim, preserve that; don't flatten it into a vague noun phrase.\n"
        "3. Read like a human-written headline: grammatical and natural, not a "
        "keyword pile-up.\n"
        "4. Drop only true filler — 'Show HN:', site names, marketing fluff, and "
        "trailing parentheticals.\n"
        "Favor brevity, but spending 3-4 extra words to keep a name, a number, or the "
        "core point is always worth it. Use no surrounding quotes and no trailing "
        "punctuation."
    )
    user = (
        "Rewrite each numbered headline below. Respond with JSON only, shaped as "
        '{"labels": ["...", "..."]} — exactly one label per input, same order.\n\n'
        + numbered
    )
    payload = {
        "model": ai.get("model", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }

    url = str(ai.get("base_url", "https://api.openai.com/v1")).rstrip("/") + "/chat/completions"
    res = http.post_json(
        url,
        payload=payload,
        timeout=float(ai.get("timeout_secs", 20.0)),
        user_agent=cfg.user_agent,
        max_bytes=cfg.max_feed_bytes,
        headers={"Authorization": f"Bearer {key}"},
        ca_bundle=cfg.ca_bundle or None,
    )
    if not res.ok:
        detail = res.error or f"HTTP {res.status}"
        log(f"ai: request failed ({detail})")
        return []

    try:
        body = json.loads(res.body.decode("utf-8", "replace"))
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
        log(f"ai: could not parse response ({e})")
        return []

    labels = parsed.get("labels") if isinstance(parsed, dict) else None
    if not isinstance(labels, list):
        log("ai: response missing a 'labels' array — skipping")
        return []
    return [str(x) for x in labels]
