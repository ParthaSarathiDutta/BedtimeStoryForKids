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
    }


def make_mock_plan(fail_first_n_internal: int = 0):
    """Returns a mock planner function. Concept always mentions every
    `must_include` phrase found in the prompt, so the deterministic Judge
    check has something real to verify rather than trivially always passing.
    """
    calls = {"n": 0}

    def mock_plan(prompt: str) -> dict[str, Any]:
        calls["n"] += 1
        must_include_line = re.search(r"MUST appear in your concept:\n(.*)", prompt)
        elements = must_include_line.group(1).strip() if must_include_line else ""
        elements = "" if elements == "(none stated)" else elements
        concept = "A brave little fox sets off on a playful adventure."
        if elements:
            concept += f" Along the way there is {elements}."
        return {
            "concept": concept,
            "protagonist": "Ember the fox",
            "setting": "a starlit meadow",
            "plot_shape": "quest/rescue",
            "open_question": "Should Ember's friend be a rabbit or an owl?",
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
