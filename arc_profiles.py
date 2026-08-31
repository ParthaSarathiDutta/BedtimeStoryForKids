"""`plot_shape` selects an arc profile: a beat sequence, not a fixed template.

REPORT.md sec 3: forcing every story through one canonical Hook-Problem-
Climax-Resolution skeleton would flatten a quiet "why is the sky blue?"
wonder story into the same shape as a dragon rescue. Instead the same
taxonomy field that drives corpus retrieval (schema.py) also selects
generation structure, coupling retrieval and structure instead of leaving
them as unrelated subsystems.

These are guidance for the Storyteller, not yet built, and for the Planner's
plan preview -- an agent may deviate from the listed beats; the Judge checks
coherence against the *profile*, not exact adherence to every beat name.
"""

from __future__ import annotations

ARC_PROFILES: dict[str, list[str]] = {
    "problem→solution": ["Hook", "Problem", "Attempt", "Solution", "Warm ending"],
    "quest/rescue": ["Hook", "Goal", "Departure", "Obstacles", "Climax", "Return", "Resolution"],
    "exploration": ["Hook", "Departure", "Encounter 1", "Encounter 2", "Encounter 3", "Discovery", "Return"],
    "discovery/learning": ["Question", "Guess 1", "Guess 2", "Imaginative possibility", "Explanation", "Satisfying close"],
    "overcome challenge": ["Hook", "Challenge", "Setback", "Effort", "Breakthrough", "Warm ending"],
    "silly/cumulative events": ["Hook", "Silly event 1", "Silly event 2", "Silly escalation", "Peak silliness", "Calm resolution"],
    # Added to the taxonomy in v1 after the annotator repeatedly reached for
    # this shape (schema.py); same beats as discovery/learning, since both are
    # curiosity-driven rather than obstacle-driven.
    "question→explanation": ["Question", "Guess 1", "Guess 2", "Imaginative possibility", "Explanation", "Satisfying close"],
}

# Used for "other" and any plot_shape value not in the map above.
_FALLBACK = ["Hook", "Problem", "Attempt", "Solution", "Warm ending"]

# Two arc rules are non-negotiable regardless of profile, and both belong in
# code rather than LLM judgment (REPORT.md sec 3): every arc ends calm -- this
# module never removes the last beat -- and beat count scales down for
# younger reading bands.
_MAX_BEATS_BY_BAND: dict[str, int] = {"5-6": 4, "7-8": 6, "9-10": 8}


def beats_for(plot_shape: str, reading_band: str | None) -> list[str]:
    """Return the beat sequence for a plot shape, capped by reading band.

    Trims from the middle, keeping the first beat (hook) and the last
    (always calm) intact, since the ending matters most for a bedtime story.
    """
    beats = list(ARC_PROFILES.get(plot_shape, _FALLBACK))
    cap = _MAX_BEATS_BY_BAND.get(reading_band or "", len(beats))
    if len(beats) <= cap or len(beats) <= 2:
        return beats
    head, tail = beats[:1], beats[-1:]
    middle = beats[1:-1][: max(cap - 2, 0)]
    return head + middle + tail
