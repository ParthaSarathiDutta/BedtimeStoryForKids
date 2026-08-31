"""Planner: turns a child's request plus corpus inspiration into a `StoryPlan`.

REPORT.md sec 2 and sec 5: the Planner's only job in Loop 1 is a short,
playful concept and enough structure -- protagonist, setting, plot_shape, at
most one open question -- for the Judge to check and, eventually, the
Storyteller to write from. It never writes prose, and it never asks about a
field the child has already resolved (sec 5.1-5.2): unresolved fields are the
Planner's own call, informed by the retrieved inspiration.
"""

from __future__ import annotations

from typing import Any, Callable

import arc_profiles
import config
import schema
from llm import LLMClient
from models import InspirationCard, StoryPlan, UserPreferences

PROMPT_TEMPLATE = """You are the Planner in a system that creates bedtime stories for a child
aged 5-10. Your job is NOT to write the story -- only to propose a short,
exciting CONCEPT plus the underlying structure a separate Storyteller will use.

Child's original request (honor every SAFE part of this; do not invent a different premise):
{initial_request}

Known preferences (the child already told us these -- do not ask about them again):
{known}

Story elements the child explicitly asked for -- these MUST appear in your concept:
{must_include}

Structural inspiration from similar published children's stories. Use these
ONLY for pacing/shape ideas -- never copy names, sentences, or specific events:
{inspiration}

Choose plot_shape from EXACTLY this list (or "other" if truly nothing fits):
{plot_shapes}

BEDTIME SAFETY (always):
- Never include death, killing, graphic injury, or real lasting harm to characters.
- If the child asks for fighting, rivalry, a showdown, or a battle: KEEP an
  age-appropriate conflict (contest, comic clash, rivalry, temporary quarrel,
  one character loses without being hurt). Do NOT erase that conflict and
  replace it with pure friendship, teamwork, or a cozy explore-together plot
  unless the child asked for friendship/teamwork.
- Soften ONLY the unsafe part. Preserve the safe underlying intent.

TRANSPARENT SAFE ADAPTATION:
- When you must change something the child EXPLICITLY asked for because it is
  not safe for bedtime, set "child_notice" to 1-2 short, warm sentences for the
  child explaining that you changed that part to keep the story safe for
  bedtime, and what you kept instead (e.g. a big showdown, but nobody gets
  badly hurt). Do NOT use words like policy, moderation, inappropriate,
  filter, or "guidelines".
- When you did NOT need to change an explicit request for safety (including
  ordinary asks like "add a puppy", "make it sillier", or "no dragon, make it
  a dinosaur"), set "child_notice" to null.

{revision_block}Respond with ONLY a JSON object:
{{
  "concept": "1-3 playful sentences pitching the story to the child, warm and exciting, aimed at a 5-10 year old",
  "protagonist": "short phrase, e.g. 'a curious little fox named Ember'",
  "setting": "short phrase",
  "plot_shape": "one value from the list above",
  "open_question": "ONE short, playful, multiple-choice question about something genuinely undecided, or null if the concept is already complete enough",
  "child_notice": "1-2 child-facing sentences if you adapted an explicit request for bedtime safety, otherwise null"
}}
"""

REVISION_BLOCK = """This is a REVISION. The previous concept was:
"{prior_concept}"
Previous protagonist: {prior_protagonist}
Previous setting: {prior_setting}

Revision guidance to address: {notes}

When revising:
- Honor every SAFE part of what the child asked in the guidance above.
- If part of their ask is unsafe for bedtime, adapt ONLY that part; keep the
  rest (including rivalry/fighting as a safe showdown if they asked for a fight).
- Keep the same character names from the previous protagonist/concept unless
  the child asked to rename or replace them.
- Set child_notice only when you made a material safety adaptation; otherwise null.

"""


def _format_known(known: dict[str, Any]) -> str:
    if not known:
        return "  (nothing specific yet -- use your judgment and the inspiration below)"
    return "\n".join(f"  - {k}: {v}" for k, v in known.items())


def _format_inspiration(cards: list[InspirationCard]) -> str:
    if not cards:
        return "  (no close matches found -- use general good judgment)"
    lines = []
    for c in cards:
        lines.append(
            f'  - "{c.title}": {c.summary} '
            f"[plot_shape={c.plot_shape}, narrative_style={c.narrative_style}, tone={c.tone}]"
        )
    return "\n".join(lines)


def build_prompt(
    preferences: UserPreferences,
    cards: list[InspirationCard],
    prior_plan: StoryPlan | None,
    revision_notes: str | None,
) -> str:
    plot_field = schema.FIELDS_BY_NAME["plot_shape"]
    revision_block = ""
    if prior_plan is not None and revision_notes:
        revision_block = REVISION_BLOCK.format(
            prior_concept=prior_plan.concept,
            prior_protagonist=prior_plan.protagonist,
            prior_setting=prior_plan.setting,
            notes=revision_notes,
        )
    return PROMPT_TEMPLATE.format(
        initial_request=preferences.initial_request or "(not recorded)",
        known=_format_known(preferences.known),
        must_include=", ".join(preferences.must_include) or "(none stated)",
        inspiration=_format_inspiration(cards),
        plot_shapes=" | ".join(plot_field.values) + " | other",
        revision_block=revision_block,
    )


def _validate(parsed: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for key in ("concept", "protagonist", "setting", "plot_shape"):
        if not isinstance(parsed.get(key), str) or not parsed[key].strip():
            problems.append(f"{key}: missing or empty")
    plot_field = schema.FIELDS_BY_NAME["plot_shape"]
    plot_shape = parsed.get("plot_shape")
    if plot_shape not in plot_field.values and plot_shape != schema.OTHER:
        problems.append(f"plot_shape: {plot_shape!r} not in vocabulary")
    open_question = parsed.get("open_question")
    if open_question is not None and not isinstance(open_question, str):
        problems.append("open_question: must be a string or null")
    notice = parsed.get("child_notice")
    if notice is not None and not isinstance(notice, str):
        problems.append("child_notice: must be a string or null")
    return problems


def create_plan(
    preferences: UserPreferences,
    cards: list[InspirationCard],
    llm: LLMClient,
    prior_plan: StoryPlan | None = None,
    revision_notes: str | None = None,
    mock_fn: Callable[[str], dict[str, Any]] | None = None,
) -> StoryPlan:
    prompt = build_prompt(preferences, cards, prior_plan, revision_notes)
    parsed = llm.complete_json(
        prompt,
        temperature=config.TEMPERATURE_PLAN,
        max_tokens=400,
        validate=_validate,
        mock_fn=mock_fn,
    )
    plot_shape = parsed["plot_shape"]
    beats = arc_profiles.beats_for(plot_shape, preferences.known.get("reading_band"))
    metadata = dict(preferences.known)
    metadata["plot_shape"] = plot_shape

    open_question = parsed.get("open_question")
    if isinstance(open_question, str) and not open_question.strip():
        open_question = None

    notice = parsed.get("child_notice")
    if isinstance(notice, str):
        notice = notice.strip() or None
    else:
        notice = None

    return StoryPlan(
        concept=parsed["concept"].strip(),
        protagonist=parsed["protagonist"].strip(),
        setting=parsed["setting"].strip(),
        plot_shape=plot_shape,
        arc_beats=beats,
        metadata=metadata,
        open_question=open_question.strip() if isinstance(open_question, str) else None,
        inspiration_ids=[c.story_id for c in cards],
        child_notice=notice,
    )
