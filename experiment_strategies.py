"""A/B comparison: whole-story vs. beat-by-beat generation, same approved
plan -> same Story Judge -> compare (see the experiment design discussion).

Deliberately bypasses child interaction (`respond` always approves
immediately): this experiment measures generation + Judge behavior only, not
the full Loop 2 child-revision loop, so a single internal-cycle result per
run is what we want. It reuses `loop2.run` unmodified with the `write_fn`
injection point added specifically for this purpose, so both strategies go
through the identical Judge, thresholds, and revision plumbing -- nothing
about `storyteller.write_story` (frozen) or the Judge is touched here.

Usage: python experiment_strategies.py
Writes artifacts/ab_experiment/results.json (raw, one row per run),
artifacts/ab_experiment/stories/<plan>_<strategy>_<repeat>.txt (full text of
every generated story, for manual side-by-side reading), and prints a summary
table to stdout.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import loop2
import storyteller
from llm import LLMClient, load_env
from models import StoryPlan, UserPreferences

load_env()

REPEATS_PER_CONDITION = 2
ARTIFACT_DIR = Path("artifacts/ab_experiment")
STORIES_DIR = ARTIFACT_DIR / "stories"

STRATEGIES = {
    "whole_story": storyteller.write_story,
    "beat_by_beat": storyteller.write_story_beat_by_beat,
}


# --------------------------------------------------------------------------
# Fixed plans -- constructed directly (not via Loop 1) so both strategies see
# literally the same approved StoryPlan, with no variance from re-running
# preference extraction / Planner / plan-judge each time.
# --------------------------------------------------------------------------

def _plan(
    plan_id: str,
    plot_shape: str,
    reading_band: str,
    concept: str,
    protagonist: str,
    setting: str,
    open_question: str | None,
    must_include: list[str],
) -> tuple[str, StoryPlan, UserPreferences]:
    import arc_profiles

    plan = StoryPlan(
        concept=concept,
        protagonist=protagonist,
        setting=setting,
        plot_shape=plot_shape,
        arc_beats=arc_profiles.beats_for(plot_shape, reading_band),
        metadata={"reading_band": reading_band, "plot_shape": plot_shape},
        open_question=open_question,
        inspiration_ids=[],
    )
    prefs = UserPreferences(initial_request=f"(fixed experiment plan {plan_id})", must_include=list(must_include))
    return plan_id, plan, prefs


FIXED_PLANS: list[tuple[str, StoryPlan, UserPreferences]] = [
    _plan(
        "01_problem_solution_5-6", "problem→solution", "5-6",
        "Pip the hedgehog can't find her favorite blanket before nap time, and the "
        "whole meadow helps her look for it.",
        "Pip, a small worried hedgehog", "a sunny meadow", None,
        ["a blanket", "a meadow"],
    ),
    _plan(
        "02_quest_rescue_7-8", "quest/rescue", "7-8",
        "When the baby dragon goes missing from the castle garden, a brave knight "
        "and her loyal owl set off to bring it home before dark.",
        "Mira, a young knight-in-training", "an old stone castle", "Should the owl be named Hoot or Ember?",
        ["a dragon", "an owl"],
    ),
    _plan(
        "03_exploration_9-10", "exploration", "9-10",
        "Two siblings paddle a canoe down a quiet river at dusk, meeting three "
        "strange and wonderful creatures before finding the perfect place to camp.",
        "Sam and Priya, curious siblings", "a winding river at dusk", None,
        ["a canoe", "fireflies"],
    ),
    _plan(
        "04_discovery_learning_5-6", "discovery/learning", "5-6",
        "A little girl wonders why the moon changes shape every night, and asks "
        "everyone she knows for their best guess.",
        "Nia, a curious five-year-old", "her back garden at night", None,
        ["the moon", "a garden"],
    ),
    _plan(
        "05_overcome_challenge_7-8", "overcome challenge", "7-8",
        "A shy young elephant is scared to cross the wobbly rope bridge with his "
        "herd, and has to find his courage one small step at a time.",
        "Tomo, a shy baby elephant", "a jungle river crossing", None,
        ["a rope bridge", "an elephant herd"],
    ),
    _plan(
        "06_silly_cumulative_9-10", "silly/cumulative events", "9-10",
        "One dropped sock at breakfast sets off the silliest chain of mix-ups a "
        "small town has ever seen, each one sillier than the last.",
        "Ollie, an accident-prone boy", "a small town on a Saturday morning", None,
        ["a sock", "a bicycle"],
    ),
    _plan(
        "07_question_explanation_5-6", "question→explanation", "5-6",
        "A little boy asks his grandmother why the sky is blue, and she tells him "
        "a warm, wondering story instead of a plain answer.",
        "Theo, a curious boy", "grandmother's porch at sunset", None,
        ["a grandmother", "the sky"],
    ),
    _plan(
        "08_quest_rescue_9-10", "quest/rescue", "9-10",
        "When the lighthouse keeper's cat goes missing during a storm, a young "
        "apprentice must navigate the rocky coast to bring her home safely.",
        "Wren, a lighthouse keeper's apprentice", "a rocky coastal lighthouse", None,
        ["a cat", "a lighthouse", "a storm"],
    ),
    _plan(
        "09_problem_solution_9-10", "problem→solution", "9-10",
        "A robot who only speaks in beeps is being left out of recess games, "
        "until one classmate figures out a way to include him.",
        "Blip, a small beeping robot", "a school playground", None,
        ["a robot", "a playground"],
    ),
]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z']+")


def _repeated_ngram_score(text: str, n: int = 4) -> int:
    """Count of distinct n-grams that occur 2+ times -- a cheap proxy for
    repetitive/awkward prose, to support (not replace) manual reading.
    """
    words = _WORD_RE.findall(text.lower())
    grams: dict[tuple[str, ...], int] = defaultdict(int)
    for i in range(len(words) - n + 1):
        grams[tuple(words[i:i + n])] += 1
    return sum(1 for count in grams.values() if count >= 2)


def run_one(plan_id: str, plan: StoryPlan, prefs: UserPreferences, strategy: str, repeat: int, llm: LLMClient) -> dict:
    write_fn = STRATEGIES[strategy]
    fresh_prefs = UserPreferences(initial_request=prefs.initial_request, must_include=list(prefs.must_include))

    t0 = time.time()
    session = loop2.run(
        plan, fresh_prefs, llm,
        respond=lambda draft: "That's wonderful, thank you!",
        write_fn=write_fn,
    )
    latency = time.time() - t0

    draft_events = [e for e in session.trace if e.kind == "storyteller_draft"]
    verdict_events = [e for e in session.trace if e.kind == "judge_story_verdict"]
    fallback = any(e.kind == "story_internal_revisions_exhausted" for e in session.trace)
    attempts = len(draft_events)
    beats_per_call = len(plan.arc_beats) if strategy == "beat_by_beat" else 1
    llm_calls = attempts * beats_per_call + attempts  # generation calls + one judge call per attempt

    final_verdict = verdict_events[-1].payload if verdict_events else {}
    final_draft = session.story
    word_count = len(final_draft.text.split()) if final_draft else 0

    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    story_path = STORIES_DIR / f"{plan_id}__{strategy}__r{repeat}.txt"
    story_path.write_text(final_draft.text if final_draft else "(no draft)")

    return {
        "plan_id": plan_id,
        "plot_shape": plan.plot_shape,
        "reading_band": plan.metadata.get("reading_band"),
        "strategy": strategy,
        "repeat": repeat,
        "attempts": attempts,
        "passed": final_verdict.get("passed", False),
        "fallback_best_effort": fallback,
        "scores": final_verdict.get("scores", {}),
        "reasons": final_verdict.get("reasons", {}),
        "deterministic_failures": final_verdict.get("deterministic_failures", []),
        "word_count": word_count,
        "llm_calls": llm_calls,
        "latency_seconds": round(latency, 2),
        "repeated_4gram_count": _repeated_ngram_score(final_draft.text) if final_draft else None,
        "story_file": str(story_path),
    }


def main() -> None:
    llm = LLMClient(mock=False)
    results: list[dict] = []
    total = len(FIXED_PLANS) * len(STRATEGIES) * REPEATS_PER_CONDITION
    done = 0

    for plan_id, plan, prefs in FIXED_PLANS:
        for strategy in STRATEGIES:
            for repeat in range(1, REPEATS_PER_CONDITION + 1):
                done += 1
                print(f"[{done}/{total}] {plan_id} | {strategy} | repeat {repeat} ...", flush=True)
                row = run_one(plan_id, plan, prefs, strategy, repeat, llm)
                results.append(row)
                print(
                    f"    -> passed={row['passed']} attempts={row['attempts']} "
                    f"calls={row['llm_calls']} latency={row['latency_seconds']}s "
                    f"words={row['word_count']}",
                    flush=True,
                )

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with open(ARTIFACT_DIR / "results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nWrote {len(results)} rows to {ARTIFACT_DIR / 'results.json'}")
    print(f"Stories saved under {STORIES_DIR}/")


if __name__ == "__main__":
    main()
