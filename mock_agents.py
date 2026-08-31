"""Deterministic mock LLM responses for exercising the pipeline without API calls.

Mirrors the mock-mode philosophy already used for corpus annotation
(`annotate_corpus._mock_response`): unit tests should verify wiring and
control flow -- did the Judge's feedback reach the Planner's next prompt, did
`must_include` actually get checked -- without spending real API calls or
depending on model output being reproducible.
"""

from __future__ import annotations

import re
from typing import Any

import schema


def mock_extract(prompt: str) -> dict[str, Any]:
    """Ignores prompt content; every test request gets the same starting preferences."""
    return {
        "preferences": {
            "story_type": ["adventure"],
            "protagonist_type": "animal",
            "tone": ["funny"],
        },
        "interest_tags": ["dragon", "space"],
        "must_include": ["a dragon"],
        "explicit_asks": [],
    }


def make_mock_plan(fail_first_n_internal: int = 0):
    """Returns a mock planner function. Concept always mentions every
    `must_include` phrase found in the prompt, so the deterministic Judge
    check has something real to verify rather than trivially always passing.

    Also exercises transparent safe adaptation: if revision notes ask for
    death/severe harm, keep rivalry but omit the harm and set child_notice;
    ordinary revision requests leave child_notice null.
    """
    calls = {"n": 0}

    def mock_plan(prompt: str) -> dict[str, Any]:
        calls["n"] += 1
        must_include_line = re.search(r"MUST appear in your concept:\n(.*)", prompt)
        elements = must_include_line.group(1).strip() if must_include_line else ""
        elements = "" if elements == "(none stated)" else elements

        prior_protag = re.search(r"Previous protagonist: (.*)", prompt)
        prior_concept = re.search(r'The previous concept was:\n"(.*)"', prompt)
        notes_match = re.search(r"Revision guidance to address: (.*)", prompt)
        notes = (notes_match.group(1) if notes_match else "").lower()
        explicit_line = re.search(r"Explicit behavioral/thematic asks[^\n]*:\n([^\n]+)", prompt)
        explicit_blob = (explicit_line.group(1).strip() if explicit_line else "").lower()
        if explicit_blob == "(none stated)":
            explicit_blob = ""

        unsafe = any(w in notes for w in ("die", "death", "kill", "stab", "murder"))
        wants_fight = any(w in notes for w in ("fight", "battle", "rival")) or any(
            w in explicit_blob for w in ("fight", "rival", "showdown", "battle")
        )

        if prior_protag and "REVISION" in prompt:
            protagonist = prior_protag.group(1).strip()
        else:
            protagonist = "Ember the fox"

        if unsafe and wants_fight:
            concept = (
                f"Join {protagonist} in a big playful showdown where they compete "
                "and one loses with a comic tumble — nobody gets badly hurt."
            )
            notice = (
                "I'm softening the fight a little to keep the story safe for bedtime. "
                "They can still have a big showdown, but nobody will be badly hurt."
            )
        elif wants_fight and "REVISION" in prompt:
            concept = (
                f"{protagonist} become fierce rivals in a dramatic contest to prove "
                "who is strongest — a big showdown with lots of energy!"
            )
            notice = None
        elif unsafe:
            concept = (
                f"Join {protagonist} on a brave adventure with a safe challenge "
                "they overcome together."
            )
            notice = (
                "I'm changing that part a little to keep the story safe for bedtime. "
                "There will still be excitement, but nobody gets badly hurt."
            )
        else:
            concept = "A brave little fox sets off on a playful adventure."
            notice = None
            if prior_concept and "REVISION" in prompt and "sillier" in notes:
                concept = "A silly little fox slips on banana peels on a playful adventure."
            if prior_concept and "REVISION" in prompt and "parrot" in notes:
                concept = "A brave little fox and a talking parrot set off on a playful adventure."

        if elements:
            concept += f" Along the way there is {elements}."

        return {
            "concept": concept,
            "protagonist": protagonist,
            "setting": "a starlit meadow",
            "plot_shape": "quest/rescue",
            "open_question": "Should Ember's friend be a rabbit or an owl?",
            "child_notice": notice,
        }

    mock_plan.calls = calls
    return mock_plan


def make_mock_judge(fail_first_n: int = 0):
    """Fails the first N calls (to exercise the internal revision loop), then passes.

    Matches judge.py's 1-5 per-dimension {score, reason} schema.
    """
    calls = {"n": 0}

    def mock_judge(prompt: str) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] <= fail_first_n:
            return {
                "engagement": {"score": 2, "reason": "Not exciting enough for a young child."},
                "clarity": {"score": 3, "reason": "Mostly clear."},
                "warmth": {"score": 3, "reason": "Acceptable but a little flat."},
                "age_appropriateness": {"score": 5, "reason": "Fine for this age."},
                "revision_feedback": "Make the concept more exciting for a young child.",
            }
        return {
            "engagement": {"score": 5, "reason": "Exciting premise."},
            "clarity": {"score": 5, "reason": "Easy to follow."},
            "warmth": {"score": 5, "reason": "Cozy and gentle."},
            "age_appropriateness": {"score": 5, "reason": "Well suited to the age range."},
            "revision_feedback": "looks good",
        }

    mock_judge.calls = calls
    return mock_judge


def make_mock_story():
    """Concept-derived mock story text, always mentioning every must_include
    phrase found in the prompt (same trick as make_mock_plan), so the
    deterministic Story Judge check has something real to verify.
    """
    def mock_story(prompt: str) -> str:
        elements_line = re.search(r"MUST appear in the story: (.*)", prompt)
        elements = elements_line.group(1).strip() if elements_line else ""
        elements = "" if elements in ("", "(nothing specific)") else elements
        body = (
            "Once there was a small adventure in a cozy corner of the world. "
            "Along the way, there was time for wonder and a little bit of fun. " * 20
        )
        if elements:
            body += f" There was also {elements}, right where they belonged. "
        body += "As the moon rose, everyone settled down, safe and warm, and drifted off to sleep."
        return body

    return mock_story


def make_mock_beat():
    """Mock for storyteller.write_story_beat_by_beat: one call per beat,
    each producing a short distinguishable sentence so tests can confirm all
    beats concatenated rather than only the first or last.
    """
    def mock_beat(prompt: str) -> str:
        beat_match = re.search(r'YOU ARE WRITING BEAT \d+ of \d+ NOW: "([^"]+)"', prompt)
        beat_name = beat_match.group(1) if beat_match else "a beat"
        elements_line = re.search(r"MUST appear somewhere across the whole story: (.*)", prompt)
        elements = elements_line.group(1).strip() if elements_line else ""
        elements = "" if elements in ("", "(nothing specific)") else elements
        text = f"In the {beat_name} part, something calm and gentle happened."
        if elements and "final" in prompt.lower():
            text += f" There was {elements}, just as hoped."
        return text

    return mock_beat


def make_mock_ending():
    """Mock for storyteller.revise_ending: returns a calm closing paragraph."""
    def mock_ending(prompt: str) -> str:
        return (
            "And so the excitement gently faded. Everyone settled under soft covers, "
            "warm and safe, as the quiet night wrapped around them like a blanket. "
            "Goodnight, little one."
        )
    return mock_ending


def make_mock_judge_story(fail_first_n: int = 0, fail_calm_ending_only: bool = True):
    """Fails the first N calls, then passes. Matches judge.py's
    DIMENSIONS_STORY {score, reason} schema.

    By default the fail case is a *primary* calm_ending failure (other
    dimensions at or above their pass bars), so Loop 2's ending-repair path
    can be exercised. Set fail_calm_ending_only=False for a broader failure.
    """
    calls = {"n": 0}

    def mock_judge_story(prompt: str) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] <= fail_first_n:
            if fail_calm_ending_only:
                return {
                    "engagement": {"score": 4, "reason": "Engaging enough."},
                    "arc_coherence": {"score": 4, "reason": "Follows a clear arc."},
                    "warmth": {"score": 4, "reason": "Warm overall."},
                    "age_appropriateness": {"score": 5, "reason": "Appropriate."},
                    "calm_ending": {"score": 2, "reason": "Ends abruptly, not calmly."},
                    "preference_adherence": {"score": 4, "reason": "Requested elements are present."},
                    "revision_feedback": "Make the ending wind down more gently.",
                }
            return {
                "engagement": {"score": 3, "reason": "Decent but not gripping."},
                "arc_coherence": {"score": 3, "reason": "Follows a basic arc."},
                "warmth": {"score": 3, "reason": "Fine."},
                "age_appropriateness": {"score": 5, "reason": "Appropriate."},
                "calm_ending": {"score": 2, "reason": "Ends abruptly, not calmly."},
                "preference_adherence": {"score": 3, "reason": "Mostly there."},
                "revision_feedback": "Make the ending wind down more gently.",
            }
        return {
            "engagement": {"score": 5, "reason": "Engaging throughout."},
            "arc_coherence": {"score": 5, "reason": "Clear beginning, middle, end."},
            "warmth": {"score": 5, "reason": "Gentle and cozy."},
            "age_appropriateness": {"score": 5, "reason": "Well suited to the age range."},
            "calm_ending": {"score": 5, "reason": "Winds down peacefully into sleep."},
            "preference_adherence": {"score": 5, "reason": "Everything requested is present."},
            "revision_feedback": "looks good",
        }

    mock_judge_story.calls = calls
    return mock_judge_story


def make_mock_feedback(responses: dict[str, dict[str, Any]] | None = None):
    """Maps a raw child response string to a fixed interpretation.

    `responses` keys are matched by substring against the prompt so a test can
    script "the child says X" without needing exact prompt text.
    """
    responses = responses or {}

    def mock_feedback(prompt: str) -> dict[str, Any]:
        for needle, result in responses.items():
            if needle in prompt:
                return result
        return {"approved": True, "intent": "approve", "extracted_element": None}

    return mock_feedback
