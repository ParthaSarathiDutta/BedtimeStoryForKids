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
