"""Runtime configuration for the storytelling pipeline.

Deliberately tiny during the Loop 1 vertical slice (see REPORT.md sec 2.2 and
the implementation-order discussion). Values that turn out to need tuning once
Loop 1 is running for real get pulled in here rather than hardcoded at call
sites, but nothing is added speculatively ahead of that.
"""

from __future__ import annotations

MODEL = "gpt-3.5-turbo"  # fixed by the assignment; same model offline and at runtime

# Generation should vary story to story; judging and extraction should be
# reproducible even when generation is not (REPORT.md sec 7).
TEMPERATURE_EXTRACT = 0.0
TEMPERATURE_PLAN = 0.7
TEMPERATURE_STORY = 0.8  # slightly higher than plan concepts: full prose benefits more from variety
TEMPERATURE_JUDGE = 0.0

MAX_ATTEMPTS = 3  # JSON-parse / validation retries per LLM call, same cap used offline

# Loop 1 (plan brainstorming) limits.
MAX_INTERNAL_REVISIONS = 3   # Planner <-> Judge, before ever reaching the child
MAX_CHILD_ROUNDS = 5         # child feedback rounds, before falling back to best-so-far

# How many corpus stories become InspirationCards for the Planner.
INSPIRATION_TOP_K = 3

# Target word count band per reading_band, shared by the Storyteller (target
# to aim for) and the Story Judge (deterministic length check), so both sides
# of that check read the same numbers instead of two copies that could drift.
_WORD_COUNT_BY_READING_BAND: dict[str, tuple[int, int]] = {
    "5-6": (150, 350),
    "7-8": (300, 550),
    "9-10": (450, 800),
}
_DEFAULT_WORD_COUNT_BAND = (250, 550)


def word_count_band(reading_band: str | None) -> tuple[int, int]:
    return _WORD_COUNT_BY_READING_BAND.get(reading_band or "", _DEFAULT_WORD_COUNT_BAND)
