"""Small, dependency-free helpers: time, text hygiene, width-aware truncation,
URL normalization and title similarity.

Everything here is deliberately allocation-light and pure — these run during
``refresh``/``sync`` (never at render time), but keeping them cheap keeps the
whole tool feeling instant.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #

def now_ts() -> int:
    return int(time.time())


def parse_date(value: Optional[str]) -> Optional[int]:
    """Parse an RSS (RFC 822) or Atom/ISO-8601 date into a unix timestamp.

    Returns ``None`` if it cannot be understood — callers treat that as
    "publication time unknown" rather than failing.
    """
    if not value:
        return None
    value = value.strip()
    # RFC 822 (RSS <pubDate>): "Wed, 02 Oct 2024 13:00:00 GMT"
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
    except (TypeError, ValueError, IndexError):
        pass
    # ISO-8601 (Atom <updated>/<published>): "2024-10-02T13:00:00Z"
    try:
        iso = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Text hygiene — make anything safe for a single terminal line
# --------------------------------------------------------------------------- #

_TAG_RE = re.compile(r"<[^>]+>")
# Control chars incl. ANSI ESC (0x1b) and other C0/C1 — stripped so a hostile
# or sloppy feed can never inject escape sequences into the status line.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WS_RE = re.compile(r"\s+")


def sanitize_text(text: Optional[str]) -> str:
    """Collapse to a single clean line: strip tags/entities/control chars."""
    if not text:
        return ""
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = _CTRL_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def char_width(ch: str) -> int:
    """Display columns for a single character (0/1/2), Unicode-aware."""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def truncate_width(text: str, max_width: int) -> str:
    """Truncate ``text`` to fit ``max_width`` terminal columns.

    Breaks on a word boundary when possible, so a headline never ends mid-word
    (e.g. "...Submit Butto" for "...Submit Button") — unless that would throw
    away more than half the budget, in which case a hard cut beats an
    over-short fragment.

    No ellipsis is appended: Claude Code renders a trailing "…" after every
    spinner verb, and that "…" doubles as our truncation indicator — matching
    the desired output exactly (e.g. "AI • Anthropic releases MCP v2…").
    """
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text
    out = []
    width = 0
    cut_index = len(text)
    for i, ch in enumerate(text):
        w = char_width(ch)
        if width + w > max_width:
            cut_index = i
            break
        out.append(ch)
        width += w
    truncated = "".join(out)
    if text[cut_index] != " ":
        last_space = truncated.rfind(" ")
        if last_space > max_width // 2:
            truncated = truncated[:last_space]
    return truncated.rstrip()


# --------------------------------------------------------------------------- #
# URL normalization (dedup key)
# --------------------------------------------------------------------------- #

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {
    "ref", "ref_src", "ref_url", "source", "cmpid", "fbclid", "gclid",
    "mc_cid", "mc_eid", "igshid", "spm", "yclid", "_hsenc", "_hsmi",
}


def normalize_url(url: str) -> str:
    """Canonicalize a URL for deduplication.

    Lowercases the host, drops scheme/fragment/``www.``/trailing slash, and
    strips tracking query params so two links to the same article collapse.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()

    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port:
        host = f"{host}:{parts.port}"

    path = parts.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not (k.lower() in _TRACKING_KEYS
                or any(k.lower().startswith(p) for p in _TRACKING_PREFIXES))
    ]
    kept.sort()
    query = urlencode(kept)

    return urlunsplit(("", host, path, query, ""))


def story_id(norm_url: str, title: str) -> str:
    """Stable, short id for a story: hash of the normalized URL, or the title
    when there is no usable URL."""
    key = norm_url if norm_url else "title:" + normalize_title(title)
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Title similarity (near-duplicate detection)
# --------------------------------------------------------------------------- #

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "how", "why", "what", "new", "now", "your", "you",
}


def normalize_title(title: str) -> str:
    return _WS_RE.sub(" ", sanitize_text(title).lower()).strip()


def _stem(word: str) -> str:
    """Very light suffix stripping so "released"/"releases"/"releasing" match."""
    for suf in ("ing", "ed", "es", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


def title_tokens(title: str) -> frozenset:
    """Significant tokens for similarity.

    Alphabetic words are stemmed; multi-character *numeric* tokens (e.g. "79"
    in "0.79") are kept verbatim so that different versions of the same product
    stay distinguishable and are not merged as duplicates.
    """
    tokens = set()
    for w in _WORD_RE.findall(title.lower()):
        if any(c.isdigit() for c in w):
            if len(w) >= 2:
                tokens.add(w)
        elif len(w) > 2 and w not in _STOP:
            tokens.add(_stem(w))
    return frozenset(tokens)


def title_similarity(a: str, b: str) -> float:
    """0.0–1.0 similarity between two titles.

    Uses token Jaccard (robust to reordering / trailing site names). Cheap and
    good enough to catch the same story syndicated across feeds.
    """
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter == 0:
        return 0.0
    return inter / len(ta | tb)
