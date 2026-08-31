"""Adapts corpus search hits into Planner-facing inspiration.

`story_search.Hit` carries only an id, title, and score -- enough to rank, not
enough to inspire a plan. This looks each hit back up in the index to surface
the small slice of information (summary, plot shape, narrative style, tone,
and which fields actually matched the request) the Planner can use as
structural inspiration, without ever exposing the story's full published text.
"""

from __future__ import annotations

from typing import Any

from models import InspirationCard
from story_search import Hit


def _index_by_id(index: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("source", {}).get("id")): record for record in index}


def build_inspiration_cards(
    hits: list[Hit],
    index: list[dict[str, Any]],
) -> list[InspirationCard]:
    by_id = _index_by_id(index)
    cards: list[InspirationCard] = []
    for hit in hits:
        record = by_id.get(hit.story_id)
        if record is None:
            continue
        md = record.get("search_metadata", {})
        # Only the fields that actually contributed to this hit's score --
        # showing every field would bury the genuinely relevant match.
        matched = {name: md.get(name) for name in hit.per_field}
        cards.append(InspirationCard(
            story_id=hit.story_id,
            title=hit.title,
            summary=str(record.get("summary", "")),
            matched_metadata=matched,
            plot_shape=str(md.get("plot_shape", "")),
            narrative_style=list(md.get("narrative_style") or []),
            tone=list(md.get("tone") or []),
        ))
    return cards
