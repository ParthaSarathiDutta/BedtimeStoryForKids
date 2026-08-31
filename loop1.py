"""Loop 1: Planner <-> Judge <-> child, brainstorming the story concept.

REPORT.md sec 2.2. Routed through `harness.AgentLoop` -- this module supplies
the domain-specific closures (how to draft, judge, present, and interpret a
`StoryPlan`) and `AgentLoop` supplies the control flow, now that Loop 1's
actual shape has been validated against real API calls (see the fixes in
`feedback_normalizer.py`, `preference_extractor.py`, and `judge.py`).

Loop 1 is cheap by design (REPORT.md sec 2.2): the child iterates on a
two-sentence concept, not a full story, so this is where most alignment
should happen.
"""

from __future__ import annotations

from typing import Any, Callable

import config
import feedback_normalizer
import judge
import planner
import preference_extractor
import story_search
from harness import AgentLoop
from inspiration import build_inspiration_cards
from llm import LLMClient
from models import JudgeResult, SessionContext, StoryPlan, UserPreferences

# Given a StoryPlan, return the child's raw text reaction. In production this
# reads from the terminal; in tests it is scripted, so the same driver code
# runs identically either way.
ChildResponder = Callable[[StoryPlan], str]


def _fetch_inspiration(preferences: UserPreferences, index: list[dict[str, Any]]):
    hits = story_search.search_stories(preferences.known, index, top_k=config.INSPIRATION_TOP_K)
    return build_inspiration_cards(hits, index)


def run(
    initial_request: str,
    index: list[dict[str, Any]],
    llm: LLMClient,
    respond: ChildResponder,
    mock_fns: dict[str, Any] | None = None,
    reading_band: str | None = None,
) -> SessionContext:
    mock_fns = mock_fns or {}

    known, must_include, dropped = preference_extractor.extract_preferences(
        initial_request, llm, mock_fn=mock_fns.get("extract"),
    )
    if reading_band:
        known["reading_band"] = reading_band  # explicit age ask wins over extractor guess
    preferences = UserPreferences(initial_request=initial_request, known=known, must_include=must_include)
    session = SessionContext(preferences=preferences)
    session.log("preferences_extracted", known=known, must_include=must_include, dropped=dropped)

    def agent_fn(prior_plan: StoryPlan | None, notes: str | None) -> StoryPlan:
        cards = _fetch_inspiration(preferences, index)
        plan = planner.create_plan(
            preferences, cards, llm,
            prior_plan=prior_plan, revision_notes=notes,
            mock_fn=mock_fns.get("plan"),
        )
        session.log(
            "planner_draft", concept=plan.concept, protagonist=plan.protagonist,
            setting=plan.setting, plot_shape=plan.plot_shape, open_question=plan.open_question,
        )
        return plan

    def judge_fn(plan: StoryPlan) -> JudgeResult:
        verdict = judge.evaluate_plan(plan, preferences, llm, mock_fn=mock_fns.get("judge_plan"))
        session.log(
            "judge_plan_verdict", passed=verdict.passed, scores=verdict.scores, reasons=verdict.reasons,
            deterministic_failures=verdict.deterministic_failures, feedback=verdict.feedback,
        )
        return verdict

    def present_fn(plan: StoryPlan) -> None:
        session.plan = plan

    def collect_fn(plan: StoryPlan) -> tuple[bool, str | None]:
        raw = respond(plan)
        response = feedback_normalizer.interpret(raw, llm, mock_fn=mock_fns.get("feedback"))
        preferences.record_plan_feedback(response)
        session.log(
            "child_response", raw_text=raw, approved=response.approved, intent=response.intent,
            extracted_element=response.extracted_element, removed_element=response.removed_element,
        )
        if response.approved:
            return True, None
        notes = (
            f'The child reacted: "{raw}" (interpreted as {response.intent}). '
            "Revise the plan to address this while keeping everything they already liked."
        )
        return False, notes

    loop = AgentLoop(agent_fn, judge_fn, present_fn, collect_fn, session, trace_kind="plan")
    loop.run()
    return session
