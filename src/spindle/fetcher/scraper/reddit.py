"""Reddit via public ``/r/<sub>/hot.json`` endpoints (no auth).

Reddit requires a descriptive User-Agent; the shared FetchContext supplies one.
Self/text posts link to their comment thread; link posts to the target URL.
"""

from __future__ import annotations

from typing import List

from ...model import RawItem

HOT = "https://www.reddit.com/r/{sub}/hot.json?limit={n}&raw_json=1"
PERMALINK = "https://www.reddit.com{path}"


def fetch(ctx, cfg, log) -> List[RawItem]:
    rc = cfg.reddit
    limit = int(rc.get("limit", 20))
    min_score = int(rc.get("min_score", 0))
    prefix = rc.get("prefix", "RDT")

    items: List[RawItem] = []
    for sub in rc.get("subreddits", []):
        data = ctx.get_json(HOT.format(sub=sub, n=limit))
        if not isinstance(data, dict):
            continue
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            if post.get("stickied") or post.get("pinned"):
                continue
            title = (post.get("title") or "").strip()
            if not title:
                continue
            score = int(post.get("score") or 0)
            if score < min_score:
                continue
            if post.get("is_self"):
                url = PERMALINK.format(path=post.get("permalink", ""))
            else:
                url = post.get("url") or PERMALINK.format(path=post.get("permalink", ""))
            items.append(RawItem(
                title=title,
                url=url,
                source="reddit",
                signal=float(score),
                published_ts=int(post.get("created_utc") or 0) or None,
                prefix_hint=prefix,
                topic_hint=sub,
            ))
    return items
