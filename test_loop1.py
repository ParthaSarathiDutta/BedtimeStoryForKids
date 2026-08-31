"""Unit checks for the runtime storytelling pipeline (Loop 1 vertical slice).

Structural/wiring tests only, using mock LLM responses (`mock_agents.py`) --
they verify that feedback actually reaches the next prompt, that deterministic
checks actually fire, and that loop-control terminates correctly. They say
nothing about real output quality; that is what the scripted real-API smoke
test in `demo_loop1.py` is for.

Run: python test_loop1.py
"""

from __future__ import annotations

import arc_profiles
import judge
import loop1
import mock_agents
import planner
import preference_extractor
import schema
from llm import LLMClient
from models import ChildResponse, StoryPlan, UserPreferences

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def check_true(name: str, cond: bool) -> None:
    if not cond:
        FAILURES.append(f"{name}: expected truthy, got falsy")


SAMPLE_INDEX = [
    {
        "source": {"id": "0001", "title": "The Brave Little Fox"},
        "search_metadata": {
            "story_type": ["adventure"], "protagonist_type": "animal",
            "setting": ["nature/farm/jungle"], "interest_tags": ["dragon", "courage"],
            "tone": ["funny"], "fantasy_level": "whimsical/personified",
            "plot_shape": "quest/rescue", "narrative_style": ["regular prose"],
            "energy_level": "playful", "reading_band": "5-6",
        },
        "summary": "A fox rescues a friend from a dragon's cave using cleverness, not force.",
        "safety": {"flags": []},
    },
    {
        "source": {"id": "0002", "title": "Goodnight Moon Meadow"},
        "search_metadata": {
            "story_type": ["bedtime"], "protagonist_type": "animal",
            "setting": ["nature/farm/jungle"], "interest_tags": ["night", "friendship"],
            "tone": ["warm/cozy"], "fantasy_level": "realistic",
            "plot_shape": "exploration", "narrative_style": ["repetitive"],
            "energy_level": "calm", "reading_band": "5-6",
        },
        "summary": "A rabbit wanders a meadow at dusk, greeting each sleepy animal.",
        "safety": {"flags": []},
    },
]


def test_preference_extractor_omits_unmentioned_fields() -> None:
    llm = LLMClient(mock=True)
    known, must_include, dropped = preference_extractor.extract_preferences(
        "I want a story about a dragon and a spaceship", llm, mock_fn=mock_agents.mock_extract,
    )
    check("story_type extracted", known.get("story_type"), ["adventure"])
    check("must_include extracted", must_include, ["a dragon"])
    check_true("unmentioned fields absent", "energy_level" not in known)
    check("nothing dropped for a clean response", dropped, [])
    check_true("no escape values leak into preferences",
              all(v not in schema.ESCAPE_VALUES for v in known.values() if isinstance(v, str)))


def test_preference_extractor_drops_cross_field_bleed() -> None:
    """The real bug found in demo_loop1.py's first live call: the model put an
    energy_level value ("mildly tense/spooky") into tone. That field must be
    dropped, not crash the whole extraction after burning all three retries.
    """
    llm = LLMClient(mock=True)

    def mock_fn(prompt: str) -> dict:
        return {
            "preferences": {
                "tone": ["mildly tense/spooky"],  # belongs to energy_level, not tone
                "protagonist_type": "animal",     # valid, must survive
            },
            "interest_tags": [],
            "must_include": [],
        }

    known, must_include, dropped = preference_extractor.extract_preferences("x", llm, mock_fn=mock_fn)
    check_true("bled value dropped, not kept", "tone" not in known)
    check("valid field survives alongside the drop", known.get("protagonist_type"), "animal")
    check_true("drop is logged", any("tone" in d for d in dropped))


def test_arc_profiles_cap_and_keep_ends() -> None:
    beats = arc_profiles.beats_for("quest/rescue", "5-6")
    check("capped to band max", len(beats) <= 4, True)
    check("hook kept", beats[0], "Hook")
    check("original ending kept", beats[-1], "Resolution")

    full = arc_profiles.beats_for("quest/rescue", "9-10")
    check("uncapped for oldest band", full, arc_profiles.ARC_PROFILES["quest/rescue"])

    fallback = arc_profiles.beats_for("not-a-real-shape", None)
    check("unknown shape falls back", fallback[-1], "Warm ending")


def test_planner_mock_wiring() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="a dragon story", must_include=["a dragon"])
    plan = planner.create_plan(prefs, [], llm, mock_fn=mock_agents.make_mock_plan())
    check_true("must_include element reached the concept", "a dragon" in plan.concept)
    check("plot_shape carried through", plan.plot_shape, "quest/rescue")
    check_true("arc beats attached", len(plan.arc_beats) > 0)


def test_judge_deterministic_tolerates_adjectives_before_the_noun() -> None:
    """Reproduces the false-failure found in demo_loop1_targeted.py: literal
    substring matching rejected "a mischievous cat" against must_include
    "a cat", burning two extra revision cycles on every populated
    must_include list until it degraded to the best-effort fallback.
    """
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x", must_include=["a cat", "a mouse", "a garden"])
    plan = StoryPlan(
        concept="Join a mischievous cat, a clever mouse, and their garden friends on an adventure.",
        protagonist="a mischievous cat", setting="a vibrant garden",
        plot_shape="quest/rescue", arc_beats=["Hook", "Resolution"], metadata={}, open_question=None,
    )
    verdict = judge.evaluate_plan(plan, prefs, llm, mock_fn=mock_agents.make_mock_judge())
    check("no false failures once articles are ignored", verdict.deterministic_failures, [])


def test_judge_deterministic_word_boundary_not_substring() -> None:
    """"cat" must not be satisfied by "caterpillar" -- word-boundary, not substring."""
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x", must_include=["a cat"])
    plan = StoryPlan(
        concept="A caterpillar inches along a leaf.", protagonist="a caterpillar",
        setting="a garden", plot_shape="exploration", arc_beats=["Hook", "Resolution"],
        metadata={}, open_question=None,
    )
    verdict = judge.evaluate_plan(plan, prefs, llm, mock_fn=mock_agents.make_mock_judge())
    check_true("caterpillar does not satisfy a request for a cat",
              any("cat" in f for f in verdict.deterministic_failures))


def test_judge_deterministic_catches_missing_element() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x", must_include=["a dragon"])
    plan = StoryPlan(
        concept="A fox goes on an adventure.",  # no dragon mentioned
        protagonist="a fox", setting="a forest", plot_shape="quest/rescue",
        arc_beats=["Hook", "Resolution"], metadata={}, open_question="What color is the fox?",
    )
    verdict = judge.evaluate_plan(plan, prefs, llm, mock_fn=mock_agents.make_mock_judge())
    check("fails on missing element", verdict.passed, False)
    check_true("failure names the missing element",
              any("dragon" in f for f in verdict.deterministic_failures))


def test_judge_deterministic_catches_bad_question() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x")
    plan = StoryPlan(
        concept="A fox goes on an adventure.", protagonist="a fox", setting="a forest",
        plot_shape="quest/rescue", arc_beats=["Hook", "Resolution"], metadata={},
        open_question="tell me more",  # not phrased as a question
    )
    verdict = judge.evaluate_plan(plan, prefs, llm, mock_fn=mock_agents.make_mock_judge())
    check("fails on malformed question", verdict.passed, False)


def test_judge_rejects_invalid_plot_shape() -> None:
    llm = LLMClient(mock=True)
    prefs = UserPreferences(initial_request="x")
    plan = StoryPlan(
        concept="A fine little concept.", protagonist="a fox", setting="a forest",
        plot_shape="funny",  # a tone value, not a plot_shape -- the cross-field bleed bug
        arc_beats=["Hook", "Resolution"], metadata={}, open_question=None,
    )
    verdict = judge.evaluate_plan(plan, prefs, llm, mock_fn=mock_agents.make_mock_judge())
    check("rejects cross-field value", verdict.passed, False)


def test_feedback_normalizer_distrusts_contradictory_approval() -> None:
    """Reproduces two real failures from demo_loop1.py's first live run.

    Both times gpt-3.5-turbo said approved=true, intent="approve" while also
    naming a specific new request in the same response -- an internally
    contradictory answer. Trusting the raw boolean silently dropped the
    child's requested change (the loop ended before "a dog" was ever added).
    """
    import feedback_normalizer
    llm = LLMClient(mock=True)

    def mock_dog(prompt: str) -> dict:
        return {"approved": True, "intent": "approve", "extracted_element": "a dog"}

    response = feedback_normalizer.interpret("can there be a dog too?", llm, mock_fn=mock_dog)
    check("contradictory approval overridden", response.approved, False)
    check("element still captured", response.extracted_element, "a dog")

    def mock_sillier(prompt: str) -> dict:
        # The model's own guidance table pairs "more_fun" with approved=false,
        # but the real run returned approved=true anyway.
        return {"approved": True, "intent": "more_fun", "extracted_element": None}

    response = feedback_normalizer.interpret("can you make it sillier?", llm, mock_fn=mock_sillier)
    check("non-approve intent is never approved, regardless of the raw flag", response.approved, False)

    def mock_clean_approve(prompt: str) -> dict:
        return {"approved": True, "intent": "approve", "extracted_element": None}

    response = feedback_normalizer.interpret("yes!", llm, mock_fn=mock_clean_approve)
    check("genuine approval still passes through", response.approved, True)


def test_preferences_replace_element_removes_and_adds() -> None:
    """"No dragon, make it a dinosaur" must drop the dragon, not just add the dinosaur."""
    prefs = UserPreferences(initial_request="x", must_include=["a dragon"])
    response = ChildResponse(
        raw_text="no dragon, make it a dinosaur",
        approved=False, intent="add_element",
        extracted_element="a dinosaur", removed_element="a dragon",
    )
    prefs.record_plan_feedback(response)
    check("dragon removed", "a dragon" not in prefs.must_include, True)
    check("dinosaur added", "a dinosaur" in prefs.must_include, True)


def test_loop1_happy_path() -> None:
    llm = LLMClient(mock=True)
    mock_fns = {
        "extract": mock_agents.mock_extract,
        "plan": mock_agents.make_mock_plan(),
        "judge_plan": mock_agents.make_mock_judge(),
        "feedback": mock_agents.make_mock_feedback(),  # always approves
    }
    session = loop1.run("a story about a dragon", SAMPLE_INDEX, llm, respond=lambda plan: "yes!", mock_fns=mock_fns)

    check_true("plan produced", session.plan is not None)
    check_true("must_include satisfied in final plan", "a dragon" in session.plan.concept)
    check_true("preferences carried the initial request", session.preferences.initial_request == "a story about a dragon")
    check("exactly one child round (immediate approval)", len(session.preferences.plan_feedback), 1)
    check_true("trace recorded key stages", {
        "preferences_extracted", "planner_draft", "judge_plan_verdict", "child_response",
    }.issubset({e.kind for e in session.trace}))


def test_loop1_child_revision_then_approve() -> None:
    llm = LLMClient(mock=True)
    responses_seen: list[str] = []

    def respond(plan) -> str:
        if not responses_seen:
            responses_seen.append("first")
            return "I want a dragon in it!"
        return "yes, I love it!"

    mock_fns = {
        "extract": mock_agents.mock_extract,
        "plan": mock_agents.make_mock_plan(),
        "judge_plan": mock_agents.make_mock_judge(),
        "feedback": mock_agents.make_mock_feedback({
            "I want a dragon in it!": {"approved": False, "intent": "add_element", "extracted_element": "a dragon"},
        }),
    }
    session = loop1.run("a story about a fox", SAMPLE_INDEX, llm, respond=respond, mock_fns=mock_fns)

    check("two child rounds recorded", len(session.preferences.plan_feedback), 2)
    check_true("dragon added to must_include from feedback", "a dragon" in session.preferences.must_include)
    check_true("final plan approved", session.preferences.plan_feedback[-1].approved)


def test_loop1_internal_judge_retry() -> None:
    """The Judge failing once must trigger a second Planner draft before the child sees anything."""
    llm = LLMClient(mock=True)
    mock_plan = mock_agents.make_mock_plan()
    mock_judge = mock_agents.make_mock_judge(fail_first_n=1)
    mock_fns = {
        "extract": mock_agents.mock_extract,
        "plan": mock_plan,
        "judge_plan": mock_judge,
        "feedback": mock_agents.make_mock_feedback(),
    }
    session = loop1.run("a story about a dragon", SAMPLE_INDEX, llm, respond=lambda plan: "yes!", mock_fns=mock_fns)

    check_true("planner called at least twice (one internal revision)", mock_plan.calls["n"] >= 2)
    check_true("judge called at least twice", mock_judge.calls["n"] >= 2)
    verdict_events = [e for e in session.trace if e.kind == "judge_plan_verdict"]
    check("first verdict failed", verdict_events[0].payload["passed"], False)
    check("final verdict passed", verdict_events[-1].payload["passed"], True)
    check("only one child round (child never saw the failed draft)",
          len(session.preferences.plan_feedback), 1)


def test_loop1_child_rounds_exhaust_gracefully() -> None:
    """The child never seeing an error, even if they never approve (REPORT.md sec 6.3)."""
    import config
    llm = LLMClient(mock=True)
    mock_fns = {
        "extract": mock_agents.mock_extract,
        "plan": mock_agents.make_mock_plan(),
        "judge_plan": mock_agents.make_mock_judge(),
        "feedback": mock_agents.make_mock_feedback({
            "never happy": {"approved": False, "intent": "other", "extracted_element": None},
        }),
    }
    session = loop1.run("x", SAMPLE_INDEX, llm, respond=lambda plan: "never happy", mock_fns=mock_fns)
    check("stops at the configured round cap", len(session.preferences.plan_feedback), config.MAX_CHILD_ROUNDS)
    check_true("still returns a usable plan, not an exception", session.plan is not None)
    check_true("exhaustion logged", any(e.kind == "plan_child_rounds_exhausted" for e in session.trace))


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
