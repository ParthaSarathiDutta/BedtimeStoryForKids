"""Judge: hybrid pass/fail on a `StoryPlan` (REPORT.md sec 6.1).

Code decides pass/fail, never the LLM directly. Deterministic checks handle
what code can verify reliably: every element the child explicitly asked for
is present, `plot_shape` is a real taxonomy value, the open question actually
reads as a question. LLM scoring handles what code cannot: engagement,
clarity, warmth, age-appropriateness of a two-sentence pitch. Requiring the
LLM to score each dimension (rather than emit one boolean) measurably reduces
the rubber-stamping `gpt-3.5-turbo` otherwise defaults to.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import config
import schema
from llm import LLMClient
from models import JudgeResult, StoryPlan, UserPreferences

DIMENSIONS = ("engagement", "clarity", "warmth", "age_appropriateness")

# 1-5 with anchors, not a free 0.0-1.0 float. A flat {0.5, 0.5, 0.5, 0.5} on
# five separate drafts in the first live smoke test showed gpt-3.5-turbo will
# lazily emit the exact midpoint on all four dimensions at once rather than
# actually differentiating them (REPORT.md sec 7 already names the fix:
# requiring a reason per dimension measurably reduces this). A discrete scale
# with a required reason forces four distinct judgments instead of one
# reflex "meh" copied four times.
PASS_SCORE = 3
_MAX_SCORE = 5

PROMPT_TEMPLATE = """You are the Judge reviewing a bedtime-story CONCEPT (not the full story
yet) for a child aged 5-10. Score honestly -- a low score is fine when
deserved; rubber-stamping produces bad stories later in the pipeline.

CONCEPT: {concept}
PROTAGONIST: {protagonist}
SETTING: {setting}
PLOT SHAPE: {plot_shape}
NEXT QUESTION FOR THE CHILD: {open_question}

WHAT THE CHILD HAS ASKED FOR SO FAR: {must_include}

Score EACH dimension separately on this 1-5 scale, with ONE short reason each.
Do not give every dimension the same score unless they genuinely deserve it --
each one is a different question:
  1 = clear failure   2 = substantial problem   3 = acceptable but weak
  4 = strong          5 = excellent

- engagement: would a child this age be excited to hear this story?
- clarity: is the concept clear, not confusing or self-contradictory?
- warmth: gentle enough for BEDTIME, not overstimulating right before sleep?
- age_appropriateness: suitable content and vocabulary level for ages 5-10?

Reply with ONLY a JSON object:
{{
  "engagement": {{"score": 1-5, "reason": "one short sentence"}},
  "clarity": {{"score": 1-5, "reason": "one short sentence"}},
  "warmth": {{"score": 1-5, "reason": "one short sentence"}},
  "age_appropriateness": {{"score": 1-5, "reason": "one short sentence"}},
  "revision_feedback": "one or two sentences of concrete guidance for whatever scored below 3, or 'looks good' if nothing did"
}}
"""


_LEADING_ARTICLES = ("a ", "an ", "the ")


def _core_phrase(phrase: str) -> str:
    """Strip a leading article so "a cat" matches "a mischievous cat".

    A real run showed literal substring matching fails almost every time:
    the Planner naturally writes "a mischievous cat", not "a cat", so
    `"a cat" in haystack` is false even though the cat is clearly there. That
    false failure burned two extra Planner/Judge cycles on every scenario
    with a populated must_include, since it only ever "passed" via the
    best-effort fallback once revisions were exhausted. This does not fix
    every phrasing mismatch (plurals, synonyms), but it fixes the common one.
    """
    p = phrase.strip().lower()
    for article in _LEADING_ARTICLES:
        if p.startswith(article):
            return p[len(article):]
    return p


def _mentions(core: str, haystack: str) -> bool:
    """Word-boundary match, not plain substring: "cat" must not match inside
    "caterpillar". Article-stripping alone would let that slip through.
    """
    if not core:
        return True
    return re.search(rf"\b{re.escape(core)}\b", haystack) is not None


def _deterministic_checks(plan: StoryPlan, preferences: UserPreferences) -> list[str]:
    """Requirements code can check reliably; kept narrow on purpose.

    Whether an ending *reads* as calm is semantic judgment the LLM should
    make -- code can only verify the requested final beat's label, which
    belongs to the Storyteller/story-level judge once that exists, not here.
    """
    failures: list[str] = []
    haystack = f"{plan.concept} {plan.protagonist} {plan.setting}".lower()

    for item in preferences.must_include:
        core = _core_phrase(item)
        if core and not _mentions(core, haystack):
            failures.append(f"missing element the child asked for: {item!r}")

    plot_field = schema.FIELDS_BY_NAME["plot_shape"]
    if plan.plot_shape not in plot_field.allowed:
        failures.append(f"plot_shape {plan.plot_shape!r} is not in the taxonomy")

    if plan.open_question is not None:
        q = plan.open_question.strip()
        if not q or len(q) > 200:
            failures.append("open_question is empty or unreasonably long")
        elif "?" not in q:
            failures.append("open_question does not read as a question")

    if not plan.concept.strip():
        failures.append("concept is empty")

    return failures


def _validate(parsed: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for dim in DIMENSIONS:
        entry = parsed.get(dim)
        if not isinstance(entry, dict):
            problems.append(f"{dim}: not an object with score/reason")
            continue
        score = entry.get("score")
        if not isinstance(score, (int, float)) or not (1 <= score <= _MAX_SCORE):
            problems.append(f"{dim}.score: {score!r} not an integer in [1, {_MAX_SCORE}]")
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            problems.append(f"{dim}.reason: missing or empty")
    return problems


def build_prompt(plan: StoryPlan, preferences: UserPreferences) -> str:
    return PROMPT_TEMPLATE.format(
        concept=plan.concept,
        protagonist=plan.protagonist,
        setting=plan.setting,
        plot_shape=plan.plot_shape,
        open_question=plan.open_question or "(none)",
        must_include=", ".join(preferences.must_include) or "(nothing specific yet)",
    )


def evaluate_plan(
    plan: StoryPlan,
    preferences: UserPreferences,
    llm: LLMClient,
    mock_fn: Callable[[str], dict[str, Any]] | None = None,
) -> JudgeResult:
    det_failures = _deterministic_checks(plan, preferences)
    prompt = build_prompt(plan, preferences)
    parsed = llm.complete_json(
        prompt,
        temperature=config.TEMPERATURE_JUDGE,
        max_tokens=400,
        validate=_validate,
        mock_fn=mock_fn,
    )
    raw_scores = {dim: int(parsed[dim]["score"]) for dim in DIMENSIONS}
    reasons = {dim: str(parsed[dim]["reason"]).strip() for dim in DIMENSIONS}
    scores = {dim: raw / _MAX_SCORE for dim, raw in raw_scores.items()}  # normalized to [0, 1]

    llm_feedback = str(parsed.get("revision_feedback", "")).strip()
    llm_passed = all(raw >= PASS_SCORE for raw in raw_scores.values())
    passed = llm_passed and not det_failures

    parts = []
    if det_failures:
        parts.append("; ".join(det_failures))
    weak = [f"{dim} ({reasons[dim]})" for dim in DIMENSIONS if raw_scores[dim] < PASS_SCORE]
    if weak:
        parts.append("weak dimensions: " + "; ".join(weak))
    if llm_feedback and llm_feedback.lower() != "looks good":
        parts.append(llm_feedback)
    feedback = " | ".join(parts) if parts else "looks good"

    return JudgeResult(
        passed=passed,
        scores=scores,
        reasons=reasons,
        deterministic_failures=det_failures,
        feedback=feedback,
    )
