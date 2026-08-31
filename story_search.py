"""Soft-scored retrieval over the annotated corpus.

A child's request ranks the corpus rather than filtering it. "cat + space +
funny" must not mean `cat AND space AND funny AND fantasy AND quest`, which over
a few-hundred-story corpus returns nothing almost every time. The score answers
"how much useful evidence does this story offer for this request", which
degrades gracefully: an unusual request still returns the most structurally
relevant stories instead of an empty list.

Field weights live in `schema.FIELDS` so the scorer and the validation
"does this field earn its weight" ablation read the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import schema


@dataclass
class Hit:
    story_id: str
    title: str
    score: float
    per_field: dict[str, float]


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return {v for v in value if isinstance(v, str)}


def field_match(field: schema.Field, query: Any, story: Any) -> float:
    """Overlap between a request's values and a story's, in [0, 1].

    Escape values contribute nothing. `other` and `uncertain` must never act as
    wildcards that match every query, which is the natural bug if they are
    treated as "unspecified".
    """
    q = _as_set(query) - set(schema.ESCAPE_VALUES)
    s = _as_set(story) - set(schema.ESCAPE_VALUES)
    if not q or not s:
        return 0.0
    return len(q & s) / len(q)


def score_story(
    preferences: dict[str, Any],
    record: dict[str, Any],
    skip_fields: Iterable[str] = (),
) -> tuple[float, dict[str, float]]:
    """Score one record against a child's preferences.

    `skip_fields` supports the ablation test in `validate_taxonomy`: drop a
    field and see whether the ranking actually changes.
    """
    skip = set(skip_fields)
    md = record.get("search_metadata", {})
    per_field: dict[str, float] = {}
    total = 0.0

    for field in schema.SCORED_FIELDS:
        if field.name in skip or field.name not in preferences:
            continue
        match = field_match(field, preferences.get(field.name), md.get(field.name))
        if match:
            per_field[field.name] = round(field.weight * match, 3)
        total += field.weight * match

    # Safety flags down-weight but never exclude. gpt-3.5-turbo proved
    # unreliable at this classification in the pilot, and a false positive
    # should cost a story some ranking rather than its existence. The
    # storytelling Judge is the real safety gate, on generated output.
    penalty = schema.retrieval_penalty(record.get("safety", {}).get("flags", []))
    return round(total * penalty, 3), per_field


def search_stories(
    preferences: dict[str, Any],
    index: list[dict[str, Any]],
    top_k: int = 3,
    skip_fields: Iterable[str] = (),
) -> list[Hit]:
    """Rank the corpus for a request.

    No story is excluded; every record is ranked. Safety only affects position.
    """
    hits: list[Hit] = []
    for record in index:
        score, per_field = score_story(preferences, record, skip_fields)
        source = record.get("source", {})
        hits.append(Hit(
            story_id=str(source.get("id", "?")),
            title=str(source.get("title", "?")),
            score=score,
            per_field=per_field,
        ))
    # Sort by score, then story_id, so ties are stable and the ablation test
    # does not report spurious ranking changes.
    hits.sort(key=lambda h: (-h.score, h.story_id))
    return hits[:top_k]
