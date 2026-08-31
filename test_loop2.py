"""Unit checks for Loop 2 (Storyteller <-> Judge <-> child) and the
end-to-end Loop 1 -> Loop 2 wiring, mirroring test_loop1.py's approach:
mock LLM responses verify wiring and control flow, not real output quality.

Run: python test_loop2.py
"""

from __future__ import annotations

import judge
import loop2
import mock_agents
import session_runner
import storyteller
from llm import LLMClient
from models import StoryDraft, StoryPlan, UserPreferences

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def check_true(name: str, cond: bool) -> None:
    if not cond:
        FAILURES.append(f"{name}: expected truthy, got falsy")


SAMPLE_PLAN = StoryPlan(
    concept="Join Ember the fox on a gentle nighttime adventure.",
    protagonist="Ember the fox", setting="a starlit meadow",
    plot_shape="quest/rescue", arc_beats=["Hook", "Goal", "Departure", "Resolution"],
    metadata={"reading_band": "5-6"}, open_question=None,
)


def test_storyteller_mock_wiring() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="a fox story", must_include=["a dragon"])
    draft = storyteller.write_story(SAMPLE_PLAN, prefs, llm, mock_fn=mock_agents.make_mock_story())
    check("strategy recorded", draft.strategy, storyteller.STRATEGY_WHOLE_STORY)
    check_true("must_include element reached the story", "a dragon" in draft.text)
    check("plan carried through", draft.plan, SAMPLE_PLAN)


def test_story_judge_deterministic_catches_missing_element() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x", must_include=["a dragon"])
    draft = StoryDraft(text="A fox went for a walk and had a lovely, calm evening. " * 30,
                        plan=SAMPLE_PLAN, strategy="whole_story")
    verdict = judge.evaluate_story(draft, prefs, llm, mock_fn=mock_agents.make_mock_judge_story())
    check("fails on missing element", verdict.passed, False)
    check_true("failure names the missing element",
              any("dragon" in f for f in verdict.deterministic_failures))


def test_story_judge_deterministic_catches_length_and_meta_artifacts() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x")
    too_short = StoryDraft(text="A very short story.", plan=SAMPLE_PLAN, strategy="whole_story")
    verdict = judge.evaluate_story(too_short, prefs, llm, mock_fn=mock_agents.make_mock_judge_story())
    check_true("too-short story fails length check",
              any("too short" in f for f in verdict.deterministic_failures))

    leaked = StoryDraft(
        text="Title: The Fox\n\n" + ("A calm story about a fox. " * 40),
        plan=SAMPLE_PLAN, strategy="whole_story",
    )
    verdict = judge.evaluate_story(leaked, prefs, llm, mock_fn=mock_agents.make_mock_judge_story())
    check_true("leaked title artifact caught",
              any("title:" in f for f in verdict.deterministic_failures))


def test_story_judge_stricter_on_calm_ending_and_preference_adherence() -> None:
    """A score of 3 (acceptable) passes ordinary dimensions but must fail
    calm_ending / preference_adherence, per the design decision that an
    approved plan is a contract and a bedtime story cannot end wound-up.
    """
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x")
    draft = StoryDraft(text="A calm little story. " * 40, plan=SAMPLE_PLAN, strategy="whole_story")

    def mock_all_threes(prompt: str) -> dict:
        return {dim: {"score": 3, "reason": "acceptable"} for dim in judge.DIMENSIONS_STORY} | {
            "revision_feedback": "looks good"
        }

    verdict = judge.evaluate_story(draft, prefs, llm, mock_fn=mock_all_threes)
    check("a flat 3 across the board must not pass", verdict.passed, False)

    def mock_strict_pass(prompt: str) -> dict:
        return {dim: {"score": 4, "reason": "strong"} for dim in judge.DIMENSIONS_STORY} | {
            "revision_feedback": "looks good"
        }

    verdict = judge.evaluate_story(draft, prefs, llm, mock_fn=mock_strict_pass)
    check("a flat 4 across the board does pass", verdict.passed, True)


def test_loop2_happy_path() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="a fox story", must_include=["a dragon"])
    mock_fns = {
        "story": mock_agents.make_mock_story(),
        "judge_story": mock_agents.make_mock_judge_story(),
        "feedback": mock_agents.make_mock_feedback(),
    }
    session = loop2.run(SAMPLE_PLAN, prefs, llm, respond=lambda draft: "yes!", mock_fns=mock_fns)

    check_true("story produced", session.story is not None)
    check_true("must_include satisfied in final story", "a dragon" in session.story.text)
    check_true("trace recorded key stages", {
        "storyteller_draft", "judge_story_verdict", "child_story_response",
    }.issubset({e.kind for e in session.trace}))


def test_loop2_internal_judge_retry() -> None:
    llm = LLMClient(mock=True)
    mock_story = mock_agents.make_mock_story()
    mock_judge_story = mock_agents.make_mock_judge_story(fail_first_n=1)
    prefs = UserPreferences(initial_request="a fox story")
    mock_fns = {
        "story": mock_story,
        "judge_story": mock_judge_story,
        "feedback": mock_agents.make_mock_feedback(),
    }
    session = loop2.run(SAMPLE_PLAN, prefs, llm, respond=lambda draft: "yes!", mock_fns=mock_fns)

    check_true("judge called at least twice (one internal revision)", mock_judge_story.calls["n"] >= 2)
    verdict_events = [e for e in session.trace if e.kind == "judge_story_verdict"]
    check("first verdict failed", verdict_events[0].payload["passed"], False)
    check("final verdict passed", verdict_events[-1].payload["passed"], True)


def test_end_to_end_loop1_then_loop2() -> None:
    """The full session_runner path: one shared SessionContext/UserPreferences
    object flows from plan brainstorming straight into story writing.
    """
    llm = LLMClient(mock=True)
    index: list[dict] = []  # empty corpus is fine -- inspiration cards are optional
    mock_fns = {
        "extract": mock_agents.mock_extract,
        "plan": mock_agents.make_mock_plan(),
        "judge_plan": mock_agents.make_mock_judge(),
        "story": mock_agents.make_mock_story(),
        "judge_story": mock_agents.make_mock_judge_story(),
        "feedback": mock_agents.make_mock_feedback(),
    }
    session = session_runner.run_full_session(
        "a story about a dragon", index, llm,
        respond_plan=lambda plan: "yes!",
        respond_story=lambda draft: "yes!",
        mock_fns=mock_fns,
    )
    check_true("plan produced", session.plan is not None)
    check_true("story produced", session.story is not None)
    check_true("must_include (from Loop 1) enforced all the way into the story",
              "a dragon" in session.story.text)
    check("same preferences object used throughout",
          session.preferences.initial_request, "a story about a dragon")


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ran {len(tests)} test groups")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print("  ", f)
        raise SystemExit(1)
    print("all passed")


if __name__ == "__main__":
    main()
