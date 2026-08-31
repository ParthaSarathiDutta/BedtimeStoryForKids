# Storytelling Strategy A/B Comparison: Whole-Story vs. Beat-by-Beat

## Design

Paired comparison, same approved `StoryPlan` fed to both strategies, same
Story Judge (`judge.evaluate_story`), same pass thresholds, same
`config.MAX_INTERNAL_REVISIONS` (3). No child interaction in this experiment
(`respond` always approves immediately) — this isolates generation + Judge
behavior, which is what differs between the two strategies. `write_story`
(whole-story) was not touched while `write_story_beat_by_beat` was built or
during this run, to avoid confounding the comparison.

- **9 fixed plans**, hand-constructed (not run through Loop 1, so both
  strategies see literally the same plan text), covering all 7 `plot_shape`
  values and all 3 `reading_band`s, each with 2-3 concrete `must_include`
  elements.
- **2 repeats per (plan, strategy)** — 36 real-API runs total (gpt-3.5-turbo).
- Beat-by-beat: one LLM call per arc beat, each given the full story-so-far
  as context; a revision regenerates every beat from scratch, mirroring how
  whole-story treats a revision (a fresh full draft), for a fair comparison.

Raw data: `results.json`. Full text of all 36 generated stories:
`stories/`. Run transcript: `run_log.txt`.

## Aggregate Results (n=18 runs per strategy)

| Metric | Whole-story | Beat-by-beat |
|---|---|---|
| Pass rate | 83.3% | 88.9% |
| Best-effort fallback rate | 16.7% | 11.1% |
| Mean internal attempts | 1.61 | 1.50 |
| **Mean LLM calls per run** | **3.22** | **9.94** |
| **Mean wall-clock latency** | **9.95s** | **16.33s** |
| Mean word count | 386 | 587 |
| Runs notably over word-count target | 0/18 | 3/18 |
| Repetition density (repeated 4-grams / word) | 0.0097 | 0.0199 (~2×) |
| Judge: engagement | 0.767 | 0.767 |
| Judge: arc_coherence | 0.767 | 0.722 |
| Judge: warmth | 0.967 | 0.944 |
| Judge: age_appropriateness | 0.867 | 0.822 |
| Judge: calm_ending | 0.967 | 0.933 |
| Judge: preference_adherence | 0.933 | 0.944 |

Per-dimension differences are within run-to-run noise (mostly ≤ 0.05, i.e.
a fraction of one 1-5 point on the Judge's scale) except word count and
repetition, which are systematic and favor whole-story.

## Per-plan pass/fail (both repeats)

| Plan | Whole-story | Beat-by-beat |
|---|---|---|
| 01 problem→solution, 5-6 | pass, pass | pass, pass |
| 02 quest/rescue, 7-8 | pass, pass | pass, pass |
| 03 exploration, 9-10 | pass, pass | pass, pass |
| 04 discovery/learning, 5-6 | pass, pass | pass, pass |
| 05 overcome challenge, 7-8 | **fail**, pass | pass, pass |
| 06 silly/cumulative, 9-10 | **fail, fail** | **fail, fail** |
| 07 question→explanation, 5-6 | pass, pass | pass, pass |
| 08 quest/rescue, 9-10 | pass, pass | pass, pass |
| 09 problem→solution, 9-10 | pass, pass | pass, pass |

Plan 06 (silly/cumulative events, 9-10) failed on **both strategies, both
repeats** — 4/4 failures, always on `calm_ending`. This is strong evidence
that this failure mode is a property of the plan/plot-shape, not of the
generation strategy: decomposing into beats did not fix it, because the
underlying problem is deciding how to land a calm ending after a silly
escalation, which is a semantic/planning problem, not a "the model lost
track of a long story" problem.

## Qualitative read (manual, side-by-side)

Read several matched pairs in full, including the two most repetitive
beat-by-beat outputs (`02_quest_rescue_7-8__beat_by_beat__r2`, 32 repeated
4-grams, and `06_silly_cumulative_9-10` beat-by-beat).

- **Beat-by-beat's characteristic failure is mid-story padding, not
  incoherence.** In `02_quest_rescue_7-8__beat_by_beat__r2`, three
  consecutive "Obstacles"-region beats each independently narrate "still
  searching the dark forest," reusing near-identical imagery ("Mira's
  determination never wavered," "the bond between them grew stronger") —
  each beat call cannot see how much runway is left, so it re-establishes
  tension it already established. The matched whole-story version covers
  the same plot beats in a third of the space with no repeated phrasing,
  because the model plans the whole arc in one pass.
- Whole-story endings read slightly more natural and address the child
  directly ("Goodnight, little one..."); beat-by-beat's final beat sometimes
  produces a slightly odd address ("Good night, brave knight") because the
  last beat is generated with only the accumulated text as context, not the
  original framing.
- Preference adherence and warmth were qualitatively indistinguishable
  between strategies — both reliably included every `must_include` element
  and read as gentle bedtime prose.
- Neither strategy handled the silly/cumulative-events plan's ending well;
  reading `06_silly_cumulative_9-10` beat-by-beat, the escalation beat
  ("the town of Willow Creek braced itself for the final, most outrageous
  twist") oversets an expectation the final beat then has to defuse in one
  short paragraph — the identical structural problem the whole-story version
  has.

## Multi-objective tradeoff

No practically meaningful quality difference was observed; all six Judge
dimensions differed by ≤0.05 (a fraction of one point on the 1–5 scale).
Beat-by-beat has a slight pass-rate edge (88.9% vs 83.3%), but that does not
compensate for the cost side:

| Objective | Winner |
|---|---|
| Quality (6 Judge dimensions) | ≈ same |
| Pass rate | ≈ same / slight beat-by-beat edge |
| LLM calls | whole-story (~3.1× fewer) |
| Latency | whole-story (~1.6× faster) |
| Length control | whole-story |
| Repetition | whole-story (~half the density) |
| **Production choice** | **whole-story** |

Beat-by-beat is also worse on the two properties that matter most for a
bedtime story: it overruns the target word-count band more often, and it is
about twice as repetitive per word — exactly the "many short calls stitched
together" failure mode decomposition strategies are supposed to avoid.

## Recommendation: whole-story as the production default

Keep `storyteller.write_story` (whole-story) as the production strategy in
`loop2.py`'s default. This is not because whole-story dominates every metric
— beat-by-beat's pass rate is slightly higher — but because the multi-
objective tradeoff clearly favors the simpler strategy. `write_story_beat_by_beat`
stays in the codebase, tested, and reachable via `loop2.run(..., write_fn=...)`
— a real, validated alternative, not a dead end — but it is not the default
because the evidence does not support its added cost.

This is the outcome the experiment design called out as the interesting
one: a more "agentic" decomposition was implemented, measured against a
simpler baseline, and rejected because the simpler approach performed
sufficiently well. Adding beat-by-beat generation to production now would
be complexity without a measured benefit.

### The more promising lead: targeted ending-only revision

The recurring, strategy-independent failure is specifically `calm_ending`
on plans whose plot shape escalates energy right up to the end (plan 06,
silly/cumulative events — 4/4 failures across both strategies, all on the
same dimension). Beat-by-beat's finer-grained control over "the last beat"
did *not* fix this, which suggests the fix is not narrative granularity but
giving the Judge's ending-specific feedback a more targeted place to land.

Per the discussion before this experiment, a promising next step (not
implemented here, per instructions) is:

```
Whole story -> Judge detects calm_ending failure -> targeted ending-only
revision (regenerate just the final beat/paragraph with the Judge's
feedback, keep the rest of the approved text fixed) -> re-judge
```

This keeps whole-story's cost profile for the common case (most plans pass
on attempt 1) while adding a cheap, surgical fix for the specific, now
well-evidenced weak spot, rather than paying beat-by-beat's 3× cost on every
single story to fix a problem that beat-by-beat doesn't actually fix.
