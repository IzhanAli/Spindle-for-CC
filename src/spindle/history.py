"""Rotation / shown-history logic.

The guarantee we can *fully* control is at the granularity of the pool: every
story is placed into a session pool once per epoch before any repeats. When the
catalog is nearly exhausted, the short final batch is topped up from the top of
the ranking so the pool is never starved, and a new epoch begins.

(Within a single session, Claude Code picks one verb at random from the pool it
was handed — that pick is Claude's, not ours. We own which stories are eligible;
Claude owns which one it draws. See README → "Rotation".)
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .model import Story


def select_pool(
    ranked: List[Story],
    history: Dict[str, Any],
    pool_size: int,
    history_size: int,
) -> Tuple[List[Story], Dict[str, Any]]:
    """Pick the next ``pool_size`` not-yet-shown stories from ``ranked``.

    Returns ``(pool, updated_history)``. When fewer than ``pool_size`` unshown
    stories remain, the remainder is taken, history resets (new epoch), and the
    pool is topped up from the top of the ranking.
    """
    if not ranked:
        return [], history

    shown = list(history.get("shown", []))
    shown_set = set(shown)
    epoch = int(history.get("epoch", 0))

    unshown = [s for s in ranked if s.id not in shown_set]

    if len(unshown) >= pool_size:
        # Enough fresh stories: hand out the next batch and keep accumulating
        # the shown-history within this epoch.
        pool = unshown[:pool_size]
        shown = shown + [s.id for s in pool]
    else:
        # Fewer than pool_size fresh stories remain. Finish the epoch with the
        # leftovers, then start a new epoch and TOP UP from the top of the
        # ranking so the pool is always full — a short pool would starve the
        # spinner of variety. The new epoch's shown-history is just this pool.
        epoch += 1
        pool = list(unshown)
        pool_ids = {s.id for s in pool}
        for s in ranked:                     # ranked is best-first
            if len(pool) >= pool_size:
                break
            if s.id not in pool_ids:
                pool.append(s)
                pool_ids.add(s.id)
        shown = [s.id for s in pool]

    if len(shown) > history_size:            # safety bound
        shown = shown[-history_size:]

    return pool, {"shown": shown, "epoch": epoch}
