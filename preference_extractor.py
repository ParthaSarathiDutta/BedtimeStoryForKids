"""Turns a child's free-text request into schema-shaped preferences.

Uses the same ten-field vocabulary as corpus annotation (`schema.py`) so a
child's stated preferences and a story's labels are directly comparable by
`story_search` -- extracting from a request and annotating a story must
produce the same shape of object (REPORT.md sec 2.1).

Deliberately extracts only what the child's words actually support. A field
the child never mentioned is simply absent from the result, which is a
different thing from `uncertain`: `uncertain` means the text was read and
found ambiguous; absent means the question was never asked. Collapsing the
two would make it impossible to tell "not yet asked" from "asked and
unclear", which the Planner needs to distinguish when deciding what to ask
next (METADATA_PREPARATION_PLAN.md sec 3.1).
"""

from __future__ import annotations

from typing import Any, Callable

import schema
from llm import LLMClient

PROMPT_TEMPLATE = """A child aged 5-10 just described a bedtime story they want. Extract ONLY
the preferences their words actually support, using ONLY the vocabularies below.

{vocabulary}

CRITICAL RULE: only include a field if the child's request supports a
confident choice. Leave a field out entirely if you are not confident --
do NOT use "uncertain" or "other" here, and do not invent a default just to
fill every field. Omission is the correct answer when the text does not say.

You must also sort everything else the child said into exactly TWO different
buckets. Getting this split right matters: `must_include` items are hard
requirements checked after every single draft, while `interest_tags` are only
soft hints for finding similar stories.

must_include -- concrete, nameable things the child explicitly asked for, that
must literally survive every future revision:
  - specific characters, named or not ("a dragon", "Sparkle", "a mouse")
  - specific objects ("a spaceship", "a magic hat")
  - a specific named place if one was given ("a garden", "the moon")
  Ask yourself: "could this story exist without this thing and still satisfy
  what the child asked for?" If no, it goes here.

interest_tags -- general topics, themes, or moods, used only to find similar
published stories. NEVER put these in must_include, even if the child
emphasized them:
  - abstract qualities: "funny", "exciting", "scary", "friendship", "adventure"
  - broad topics without a specific instance: "animals", "space", "school"
  A story satisfies "I want something about friendship" in countless
  different ways; it does not need a literal object called "friendship".

Example: "a cat and a mouse who are friends in a garden" ->
  must_include: ["a cat", "a mouse", "a garden"]
  interest_tags: ["friendship", "garden"]
(friendship is the theme, not a nameable thing -- it is a tag, not a requirement)

CHILD'S REQUEST: {request}

Reply with ONLY a JSON object, no prose before or after:

{{
  "preferences": {{
    ... only the fields you are confident about, using the exact field names above ...
  }},
  "interest_tags": ["..."],
  "must_include": ["..."]
}}
"""


def build_prompt(request: str) -> str:
    return PROMPT_TEMPLATE.format(
        vocabulary=schema.vocabulary_prompt_block(),
        request=request.strip(),
    )


def _validate(parsed: dict[str, Any]) -> list[str]:
    """Structural checks only -- NOT vocabulary compliance.

    An earlier version rejected the whole response and burned all three
    retries whenever one field held an off-vocabulary value. In real use the
    very first call did exactly the cross-field bleed the annotation prompt
    was hardened against (`tone: "mildly tense/spooky"`, an `energy_level`
    value), which crashed the session instead of just dropping one field.
    That value is recoverable with a dictionary lookup; it should never have
    cost a retry, let alone a crash. `_normalize_preferences` below is where
    off-vocabulary values actually get handled, by omission.
    """
    problems: list[str] = []
    if not isinstance(parsed.get("preferences"), dict):
        problems.append("preferences: not an object")
    if not isinstance(parsed.get("interest_tags", []), list):
        problems.append("interest_tags: not a list")
    if not isinstance(parsed.get("must_include", []), list):
        problems.append("must_include: not a list")
    return problems


def _normalize_preferences(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Clean values without `schema.normalize_metadata`'s "fill every field
    with a default" behavior, which is correct for annotation but wrong here:
    an absent preference must stay absent, not become `uncertain`.

    A value that survives normalization but still isn't in the field's
    vocabulary (cross-field bleed, or the model inventing a value) is dropped
    rather than kept as a bad literal: a value `story_search` can never match
    is worse than an absent field, since absent at least reads honestly as
    "not asked" instead of silently never matching anything.
    """
    out: dict[str, Any] = {}
    dropped: list[str] = []
    for name, value in raw.items():
        field = schema.FIELDS_BY_NAME.get(name)
        if field is None:
            dropped.append(f"{name}: not a known field")
            continue
        if field.cardinality == "multi":
            items = value if isinstance(value, list) else [value]
            cleaned = []
            for item in items:
                if not isinstance(item, str):
                    continue
                new = schema.normalize_value(name, item)
                if not new or new in schema.ESCAPE_VALUES:
                    continue
                if not field.open_vocabulary and new not in field.values:
                    dropped.append(f"{name}: {item!r} not in vocabulary, dropped")
                    continue
                if new not in cleaned:
                    cleaned.append(new)
            if cleaned:
                out[name] = cleaned
        else:
            v = value[0] if isinstance(value, list) and value else value
            if not isinstance(v, str):
                continue
            new = schema.normalize_value(name, v)
            if new in schema.ESCAPE_VALUES:
                continue
            if new not in field.values:
                dropped.append(f"{name}: {v!r} not in vocabulary, dropped")
                continue
            out[name] = new
    return out, dropped


def extract_preferences(
    request: str,
    llm: LLMClient,
    mock_fn: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Returns (preferences shaped like search_metadata, must_include, dropped-value log)."""
    prompt = build_prompt(request)
    parsed = llm.complete_json(
        prompt,
        temperature=0.0,  # extraction should be reproducible, not creative
        max_tokens=400,
        validate=_validate,
        mock_fn=mock_fn,
    )
    prefs = dict(parsed.get("preferences", {}))
    tags = parsed.get("interest_tags") or []
    if tags:
        prefs["interest_tags"] = list(tags)
    known, dropped = _normalize_preferences(prefs)
    must_include = [str(x).strip() for x in (parsed.get("must_include") or []) if str(x).strip()]
    return known, must_include, dropped
