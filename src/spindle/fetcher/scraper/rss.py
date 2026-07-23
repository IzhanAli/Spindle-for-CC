"""RSS 2.0 / Atom parsing via the stdlib ``xml.etree``.

Namespace-agnostic (we compare local tag names), tolerant of malformed feeds
(a parse error yields an empty list — "corrupt feed → ignore"), and bounded in
how many entries it takes from any single feed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from ...model import RawItem
from ...util import parse_date, sanitize_text

MAX_ITEMS_PER_FEED = 40


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(el: ET.Element) -> str:
    if el.text and el.text.strip():
        return el.text
    return "".join(el.itertext())


def parse_feed(body: bytes, prefix: Optional[str], topic: Optional[str] = None,
               source: str = "rss") -> List[RawItem]:
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        return []

    items: List[RawItem] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue

        title = ""
        link = ""
        published: Optional[int] = None

        for child in el:
            name = _local(child.tag)
            if name == "title" and not title:
                title = _text(child)
            elif name == "link":
                href = child.get("href")            # Atom
                if href:
                    rel = child.get("rel")
                    if rel in (None, "alternate") and not link:
                        link = href
                elif child.text and not link:       # RSS
                    link = child.text
            elif name in ("pubdate", "published", "updated", "date") and published is None:
                published = parse_date(child.text)

        title = sanitize_text(title)
        if not title:
            continue

        items.append(RawItem(
            title=title,
            url=(link or "").strip(),
            source=source,
            prefix_hint=prefix,
            topic_hint=topic,
            published_ts=published,
        ))
        if len(items) >= MAX_ITEMS_PER_FEED:
            break

    return items


def fetch(ctx, cfg, log) -> List[RawItem]:
    items: List[RawItem] = []
    for feed in cfg.rss:
        url = feed.get("url")
        if not url:
            continue
        body = ctx.conditional_get(url)
        if body is None:
            continue
        items += parse_feed(body, feed.get("prefix"), feed.get("topic"))
    return items
