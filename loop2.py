"""Loop 2: Storyteller <-> Judge <-> child, writing and refining the full story.

REPORT.md sec 2.2. Structurally mirrors `loop1.py`, now through the same
`harness.AgentLoop` validated there. This is the first real second consumer
of that abstraction, which is itself a check on the harness: if the contract
(`agent_fn`/`judge_fn`/`present_fn`/`collect_fn`, `passed`/`feedback` off the
verdict) needed to change to fit a genuinely different artifact type, that
would mean generalizing was premature. It did not need to change.

When the Judge fails primarily on `calm_ending`, the next internal revision
uses `storyteller.revise_ending` (surgical closing-section repair) rather than
regenerating the whole story -- the A/B experiment showed beat-by-beat did not
fix that failure mode, so the exception path is a targeted Storyteller
operation, not a new agent.
"""

from __future__ import annotations

from typing import Any, Callable

import feedback_normalizer
import judge
import storyteller
from harness import AgentLoop
from llm import LLMClient
from models import JudgeResult, SessionContext, StoryDraft, StoryPlan, UserPreferences

# Given a StoryDraft, return the child's raw text reaction to hearing the story.
StoryResponder = Callable[[StoryDraft], str]


def run(
    plan: StoryPlan,
    preferences: UserPreferences,
    llm: LLMClient,
    respond: StoryResponder,
    session: SessionContext | None = None,
    mock_fns: dict[str, Any] | None = None,
    write_fn: Callable[..., StoryDraft] = storyteller.write_story,
    ending_repair: bool = True,
) -> SessionContext:
    """`write_fn` defaults to the whole-story strategy. It exists as an
    injection point so `experiment_strategies.py` can run the identical Loop 2
    control flow (same Judge, same thresholds, same revision plumbing) against
    `storyteller.write_story_beat_by_beat` without touching this module or
    either strategy's prompt -- see the A/B comparison discussion.

    `ending_repair` (default True) enables the calm_ending exception path:
    when the previous verdict fails primarily on calm_ending, the next
    revision calls `revise_ending` instead of a full rewrite. Disable it in
    experiments that need to measure full-regeneration as a baseline.
    """
    mock_fns = mock_fns or {}
    session = session or SessionContext(preferences=preferences, plan=plan)
    last_verdict: list[JudgeResult | None] = [None]

    def agent_fn(prior_draft: StoryDraft | None, notes: str | None) -> StoryDraft:
        if (
            ending_repair
            and prior_draft is not None
            and last_verdict[0] is not None
            and judge.is_primarily_calm_ending_failure(last_verdict[0])
        ):
            draft = storyteller.revise_ending(
                prior_draft,
                preferences,
                llm,
                judge_feedback=notes or last_verdict[0].feedback,
                mock_fn=mock_fns.get("ending") or mock_fns.get("story"),
            )
            session.log(
                "storyteller_ending_repair",
                strategy=draft.strategy,
                word_count=len(draft.text.split()),
            )
            return draft

        draft = write_fn(
            plan, preferences, llm, revision_notes=notes, mock_fn=mock_fns.get("story"),
        )
        session.log("storyteller_draft", strategy=draft.strategy, word_count=len(draft.text.split()))
        return draft

    def judge_fn(draft: StoryDraft) -> JudgeResult:
        verdict = judge.evaluate_story(draft, preferences, llm, mock_fn=mock_fns.get("judge_story"))
        last_verdict[0] = verdict
        session.log(
            "judge_story_verdict", passed=verdict.passed, scores=verdict.scores, reasons=verdict.reasons,
            deterministic_failures=verdict.deterministic_failures, feedback=verdict.feedback,
        )
        return verdict

    def present_fn(draft: StoryDraft) -> None:
        session.story = draft

    def collect_fn(draft: StoryDraft) -> tuple[bool, str | None]:
        raw = respond(draft)
        response = feedback_normalizer.interpret(raw, llm, mock_fn=mock_fns.get("feedback"))
        preferences.record_story_feedback(response)
        session.log(
            "child_story_response", raw_text=raw, approved=response.approved, intent=response.intent,
            extracted_element=response.extracted_element, removed_element=response.removed_element,
        )
        if response.approved:
            return True, None
        notes = (
            f'The child reacted: "{raw}" (interpreted as {response.intent}). '
            "Revise the story to address this while keeping everything they already liked "
            "and everything from the approved plan."
        )
        return False, notes

    loop = AgentLoop(agent_fn, judge_fn, present_fn, collect_fn, session, trace_kind="story")
    loop.run()
    return session
