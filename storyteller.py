"""Storyteller: turns an approved `StoryPlan` into full prose (REPORT.md sec 2, 3).

Whole-story generation is the baseline strategy implemented here: one LLM
call from an approved plan straight to finished prose. REPORT.md sec 1.1
argued beat-by-beat generation would be necessary given `gpt-3.5-turbo`'s
limits, but that was a hypothesis pending a real comparison, not a measured
fact -- per the sequencing agreed before building Loop 2, whole-story is
implemented and validated first, and beat-by-beat is only added afterward as
an explicit A/B comparison against this baseline, not a parallel commitment.
"""

from __future__ import annotations

from typing import Callable

import config
from llm import LLMClient
from models import StoryDraft, StoryPlan, UserPreferences

STRATEGY_WHOLE_STORY = "whole_story"

PROMPT_TEMPLATE = """You are the Storyteller. Write a complete bedtime story in plain prose for
a child aged 5-10, based on the plan below, which the child has ALREADY
approved -- it is a contract, not a suggestion. You may elaborate freely, but
you may not silently change or drop anything in it.

CONCEPT: {concept}
PROTAGONIST: {protagonist}
SETTING: {setting}
SUGGESTED BEATS (a guide for pacing, not a rigid template to name explicitly): {beats}

ELEMENTS THE CHILD EXPLICITLY ASKED FOR -- these MUST appear in the story: {must_include}

Write in a warm, gentle voice. Aim for roughly {min_words}-{max_words} words.
The ending is the single most important part: it MUST be calm and reassuring
-- no cliffhangers, no unresolved excitement, nothing that would wind a child
up right before sleep, even if the middle of the story is adventurous.

{revision_block}Write ONLY the story text -- no title, no headings, no beat labels, no notes
before or after the story.
"""

REVISION_BLOCK = """This is a REVISION of a previous draft. Feedback to address: {notes}

"""


def build_prompt(plan: StoryPlan, preferences: UserPreferences, revision_notes: str | None) -> str:
    min_words, max_words = config.word_count_band(plan.metadata.get("reading_band"))
    revision_block = REVISION_BLOCK.format(notes=revision_notes) if revision_notes else ""
    return PROMPT_TEMPLATE.format(
        concept=plan.concept,
        protagonist=plan.protagonist,
        setting=plan.setting,
        beats=" -> ".join(plan.arc_beats),
        must_include=", ".join(preferences.must_include) or "(nothing specific)",
        min_words=min_words,
        max_words=max_words,
        revision_block=revision_block,
    )


def write_story(
    plan: StoryPlan,
    preferences: UserPreferences,
    llm: LLMClient,
    revision_notes: str | None = None,
    mock_fn: Callable[[str], str] | None = None,
) -> StoryDraft:
    prompt = build_prompt(plan, preferences, revision_notes)
    text = llm.complete_text(
        prompt,
        temperature=config.TEMPERATURE_STORY,
        max_tokens=1200,
        mock_fn=mock_fn,
    )
    return StoryDraft(text=text.strip(), plan=plan, strategy=STRATEGY_WHOLE_STORY)


# --------------------------------------------------------------------------
# Beat-by-beat strategy -- kept as a fully separate code path from
# write_story above, which must stay frozen while this is built and measured
# (see the A/B comparison in experiment_strategies.py): touching
# write_story's prompt while building this would confound the comparison.
# --------------------------------------------------------------------------

STRATEGY_BEAT_BY_BEAT = "beat_by_beat"

BEAT_PROMPT_TEMPLATE = """You are the Storyteller, continuing a bedtime story one beat at a time.

APPROVED PLAN (a contract):
- Concept: {concept}
- Protagonist: {protagonist}
- Setting: {setting}
Elements the child explicitly asked for -- these MUST appear somewhere across the whole story: {must_include}

FULL BEAT SEQUENCE FOR THIS STORY: {all_beats}
YOU ARE WRITING BEAT {beat_index} of {beat_count} NOW: "{current_beat}"

STORY SO FAR:
{story_so_far}

{revision_block}Write ONLY the prose for THIS beat -- a short paragraph or two, continuing
directly on from the story so far, in a warm, gentle voice for a child aged
5-10. Do not restate previous events, do not repeat phrasing already used
above, and do not include beat labels, headings, or notes -- just the
continuing story prose.
{last_beat_instruction}
"""

_LAST_BEAT_INSTRUCTION = (
    "This is the FINAL beat -- it MUST end the WHOLE story calm and "
    "reassuring: no cliffhangers, no unresolved excitement, nothing that "
    "would wind a child up right before sleep."
)


def _build_beat_prompt(
    plan: StoryPlan,
    preferences: UserPreferences,
    beat_name: str,
    beat_index: int,
    beat_count: int,
    story_so_far: str,
    revision_notes: str | None,
) -> str:
    revision_block = (
        f"REVISION GUIDANCE (apply wherever it is relevant to this beat): {revision_notes}\n\n"
        if revision_notes else ""
    )
    return BEAT_PROMPT_TEMPLATE.format(
        concept=plan.concept,
        protagonist=plan.protagonist,
        setting=plan.setting,
        must_include=", ".join(preferences.must_include) or "(nothing specific)",
        all_beats=" -> ".join(plan.arc_beats),
        beat_index=beat_index,
        beat_count=beat_count,
        current_beat=beat_name,
        story_so_far=story_so_far.strip() or "(the story has not started yet -- this is the opening beat)",
        revision_block=revision_block,
        last_beat_instruction=_LAST_BEAT_INSTRUCTION if beat_index == beat_count else "",
    )


def write_story_beat_by_beat(
    plan: StoryPlan,
    preferences: UserPreferences,
    llm: LLMClient,
    revision_notes: str | None = None,
    mock_fn: Callable[[str], str] | None = None,
) -> StoryDraft:
    """One LLM call per beat, each given the running story-so-far as context
    for coherence (REPORT.md sec 3, sec 6.4: "beat-level generation with a
    running summary"). A revision regenerates every beat from scratch with
    the same guidance visible to each call, rather than patching a single
    beat -- simpler, and consistent with how the whole-story strategy treats
    a revision (a fresh full draft), which is what a fair comparison needs.
    """
    beats = plan.arc_beats or ["Hook", "Resolution"]
    parts: list[str] = []
    for i, beat_name in enumerate(beats, start=1):
        prompt = _build_beat_prompt(
            plan, preferences, beat_name, i, len(beats), "\n\n".join(parts), revision_notes,
        )
        text = llm.complete_text(
            prompt,
            temperature=config.TEMPERATURE_STORY,
            max_tokens=350,
            mock_fn=mock_fn,
        )
        parts.append(text.strip())
    return StoryDraft(text="\n\n".join(parts).strip(), plan=plan, strategy=STRATEGY_BEAT_BY_BEAT)
