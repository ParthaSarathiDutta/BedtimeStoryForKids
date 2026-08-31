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


def test_storyteller_beat_by_beat_mock_wiring() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x")
    draft = storyteller.write_story_beat_by_beat(SAMPLE_PLAN, prefs, llm, mock_fn=mock_agents.make_mock_beat())
    check("strategy recorded", draft.strategy, storyteller.STRATEGY_BEAT_BY_BEAT)
    for beat in SAMPLE_PLAN.arc_beats:
        check_true(f"beat {beat!r} present in concatenated text", beat in draft.text)


def test_loop2_supports_pluggable_write_fn() -> None:
    """The A/B comparison depends on loop2 running the identical control flow
    for both strategies -- confirm write_fn actually gets used, not silently
    ignored in favor of the default whole-story path.
    """
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x")
    mock_fns = {
        "story": mock_agents.make_mock_beat(),
        "judge_story": mock_agents.make_mock_judge_story(),
        "feedback": mock_agents.make_mock_feedback(),
    }
    session = loop2.run(
        SAMPLE_PLAN, prefs, llm, respond=lambda draft: "yes!", mock_fns=mock_fns,
        write_fn=storyteller.write_story_beat_by_beat,
    )
    check("beat_by_beat strategy actually used", session.story.strategy, storyteller.STRATEGY_BEAT_BY_BEAT)


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
        "ending": mock_agents.make_mock_ending(),
        "feedback": mock_agents.make_mock_feedback(),
    }
    session = loop2.run(SAMPLE_PLAN, prefs, llm, respond=lambda draft: "yes!", mock_fns=mock_fns)

    check_true("judge called at least twice (one internal revision)", mock_judge_story.calls["n"] >= 2)
    verdict_events = [e for e in session.trace if e.kind == "judge_story_verdict"]
    check("first verdict failed", verdict_events[0].payload["passed"], False)
    check("final verdict passed", verdict_events[-1].payload["passed"], True)
    check_true(
        "calm_ending-only failure used ending repair",
        any(e.kind == "storyteller_ending_repair" for e in session.trace),
    )


def test_split_body_and_ending_preserves_paragraphs() -> None:
    text = "Para one stays.\n\nPara two stays.\n\nPara three is the ending."
    body, ending = storyteller.split_body_and_ending(text)
    check("body keeps earlier paragraphs", body, "Para one stays.\n\nPara two stays.")
    check("ending is last paragraph", ending, "Para three is the ending.")

    long = "\n\n".join(f"P{i}." for i in range(1, 9))  # 8 paragraphs -> last 2 (~25%)
    body, ending = storyteller.split_body_and_ending(long)
    check("long-story body keeps first 6", body, "\n\n".join(f"P{i}." for i in range(1, 7)))
    check("long-story ending is last 2", ending, "P7.\n\nP8.")


def test_revise_ending_preserves_body() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x", must_include=["a dragon"])
    draft = StoryDraft(
        text=(
            "Once there was a fox who met a dragon in a meadow.\n\n"
            "They played until the stars came out.\n\n"
            "Then suddenly everything was loud and exciting again!"
        ),
        plan=SAMPLE_PLAN,
        strategy=storyteller.STRATEGY_WHOLE_STORY,
    )
    repaired = storyteller.revise_ending(
        draft, prefs, llm, judge_feedback="ending too exciting",
        mock_fn=mock_agents.make_mock_ending(),
    )
    check("strategy tagged ending_repair", repaired.strategy, storyteller.STRATEGY_ENDING_REPAIR)
    check_true("body preserved", repaired.text.startswith(
        "Once there was a fox who met a dragon in a meadow.\n\nThey played until the stars came out."
    ))
    check_true("new calm ending present", "Goodnight" in repaired.text or "settled" in repaired.text)
    check_true("old loud ending gone", "loud and exciting" not in repaired.text)


def test_is_primarily_calm_ending_failure() -> None:
    from models import JudgeResult

    calm_only = JudgeResult(
        passed=False,
        scores={
            "engagement": 0.8, "arc_coherence": 0.8, "warmth": 0.8,
            "age_appropriateness": 1.0, "calm_ending": 0.4, "preference_adherence": 0.8,
        },
        reasons={},
        deterministic_failures=[],
        feedback="calm ending weak",
    )
    check_true("calm-only failure detected", judge.is_primarily_calm_ending_failure(calm_only))

    also_pref = JudgeResult(
        passed=False,
        scores={
            "engagement": 0.8, "arc_coherence": 0.8, "warmth": 0.8,
            "age_appropriateness": 1.0, "calm_ending": 0.4, "preference_adherence": 0.4,
        },
        reasons={},
        deterministic_failures=[],
        feedback="calm and prefs weak",
    )
    check("pref failure blocks ending-only path", judge.is_primarily_calm_ending_failure(also_pref), False)

    det = JudgeResult(
        passed=False,
        scores=dict(calm_only.scores),
        reasons={},
        deterministic_failures=["missing element the child asked for: 'a dragon'"],
        feedback="missing dragon",
    )
    check("deterministic failure blocks ending-only path", judge.is_primarily_calm_ending_failure(det), False)


def test_loop2_skips_ending_repair_when_broader_failure() -> None:
    """If more than calm_ending fails, Loop 2 must full-regenerate, not patch."""
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x")
    mock_fns = {
        "story": mock_agents.make_mock_story(),
        "judge_story": mock_agents.make_mock_judge_story(fail_first_n=1, fail_calm_ending_only=False),
        "ending": mock_agents.make_mock_ending(),
        "feedback": mock_agents.make_mock_feedback(),
    }
    session = loop2.run(SAMPLE_PLAN, prefs, llm, respond=lambda draft: "yes!", mock_fns=mock_fns)
    check_true(
        "broader failure did not use ending repair",
        not any(e.kind == "storyteller_ending_repair" for e in session.trace),
    )
    check_true(
        "full rewrite path still taken",
        sum(1 for e in session.trace if e.kind == "storyteller_draft") >= 2,
    )


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
