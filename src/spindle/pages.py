"""Article descriptions — a summary for a story whose feed came with none.

Most Hacker News, Lobsters and Reddit link posts carry no text of their own,
and some feeds give titles alone. For those, a refresh reads the top of the
article's page and keeps the description its publisher wrote for link
previews: ``og:description``, else ``twitter:description``, else the page's
meta description.

Bounded on every side: only stories that made the cache and still lack a
summary, best-ranked first and at most ``MAX_PAGES`` per refresh, several at
once; each page is read once in a story's life (``Story.page_checked``), and
only up to ``MAX_PAGE_BYTES``, since the head comes first. Like all of
spindle's network use, this runs only in the detached refresh and never
raises. ``page_summaries = false`` keeps spindle to the feeds themselves.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

from .fetcher.common import MAX_PARALLEL_REQUESTS
from .http import fetch
from .model import Story
from .util import excerpt

Logger = Callable[[str], None]

MAX_PAGES = 30                    # pages read per refresh
MAX_PAGE_BYTES = 512 * 1024       # the <head> is near the top: read no further
_CHUNK = 16 * 1024                # parse this much at a time, stopping at <body>

# Hosts whose story pages describe no article (the aggregators' own comment
# pages, whose posts already carry their text) or turn scripts away.
_SKIP_HOSTS = ("news.ycombinator.com", "lobste.rs", "reddit.com", "redd.it")
_SKIP_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                    ".mp3", ".mp4", ".mov", ".webm", ".zip", ".gz")
# Best first: og:description is written per article; a plain meta
# description is sometimes the whole site's.
_KEYS = ("og:description", "twitter:description", "description")
_HEADER_CHARSET_RE = re.compile(r"charset=[\"']?([\w.:-]+)", re.I)
_META_CHARSET_RE = re.compile(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", re.I)


class _HeadParser(HTMLParser):
    """Collects a page's description <meta> tags until its head ends."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: Dict[str, str] = {}
        self.done = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "body":
            self.done = True
        if tag != "meta" or self.done:
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        key = (attr.get("property") or attr.get("name") or "").strip().lower()
        content = attr.get("content", "").strip()
        if key in _KEYS and content and key not in self.found:
            self.found[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.done = True


def page_description(page: str) -> str:
    """The best preview description in an HTML page's head, or ""."""
    parser = _HeadParser()
    for start in range(0, len(page), _CHUNK):
        parser.feed(page[start:start + _CHUNK])
        if parser.done:
            break
    return next((parser.found[k] for k in _KEYS if k in parser.found), "")


def _decode(body: bytes, content_type: Optional[str]) -> str:
    m = _HEADER_CHARSET_RE.search(content_type or "") or _META_CHARSET_RE.search(body[:4096])
    charset = m.group(1) if m else "utf-8"
    if isinstance(charset, bytes):
        charset = charset.decode("ascii", "replace")
    try:
        return body.decode(charset, "replace")
    except LookupError:                   # a charset Python doesn't know
        return body.decode("utf-8", "replace")


def wanted(story: Story) -> bool:
    """True if ``story`` has no summary yet and its link is a page worth reading."""
    if story.summary or story.page_checked:
        return False
    try:
        parts = urlsplit(story.url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    if any(host == h or host.endswith("." + h) for h in _SKIP_HOSTS):
        return False
    return not parts.path.lower().endswith(_SKIP_EXTENSIONS)


def _read(story: Story, cfg) -> Optional[str]:
    """The summary ``story``'s page gives ("" for none), or None when the
    answer may change (offline, a timeout, rate-limited, a server error), to
    try again next refresh."""
    res = fetch(
        story.url,
        timeout=cfg.request_timeout_secs,
        user_agent=cfg.user_agent,
        max_bytes=MAX_PAGE_BYTES,
        extra_headers={"Accept": "text/html,application/xhtml+xml"},
        ca_bundle=cfg.ca_bundle,
    )
    if res.status is None or res.status == 429 or res.status >= 500:
        return None
    if not res.ok or "html" not in (res.content_type or "text/html").lower():
        return ""
    try:
        return excerpt(page_description(_decode(res.body, res.content_type)), story.title)
    except Exception:                     # a page no parser could make sense of
        return ""


def fill_summaries(stories: List[Story], cfg, log: Logger = lambda _m: None) -> int:
    """Give stories without a summary their article page's description.

    ``stories`` is best-ranked first. Returns how many got one.
    """
    todo = [s for s in stories if wanted(s)][:MAX_PAGES]
    if not todo:
        return 0
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_REQUESTS, len(todo))) as pool:
        found = list(pool.map(lambda s: _read(s, cfg), todo))
    n = 0
    for story, summary in zip(todo, found):
        if summary is None:
            continue
        story.page_checked = True
        if summary:
            story.summary = summary
            n += 1
    log(f"pages: {n} of {len(todo)} article pages gave a summary")
    return n
