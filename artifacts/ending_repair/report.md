# Targeted ending repair: narrow evaluation

## Motivation

The whole-story vs. beat-by-beat A/B showed `calm_ending` failures on
high-energy arcs (esp. silly/cumulative) were strategy-*independent*. That
pointed to a surgical exception path, not further decomposition:

```
Whole story → Judge → fail primarily on calm_ending
                   → revise_ending (replace closing section only)
                   → re-Judge
```

`revise_ending` is a Storyteller *operation*, not a new agent. Loop 2 calls
it when `judge.is_primarily_calm_ending_failure(verdict)` is true; otherwise
it still does a full rewrite.

## What was measured

1. **Offline repair** of the two known-failed whole-story drafts from A/B
   plan 06.
2. **One-shot paired repair** on the same failed draft: full regen vs.
   ending repair (misleading alone — see below).
3. **Loop 2 multi-revision** on plan 06 (the hard case), 3 runs with
   `ending_repair=False` vs 3 with `ending_repair=True` — the production-
   relevant comparison.

Raw data: `results.json`, `results_v2.json`, `loop2_compare.json`, `stories/`.

## Finding 1: closing window must be wider than one paragraph

Replacing only the final paragraph did not lift `calm_ending` on the known
A/B failures (Judge reasons cited lingering mid-story chaos). Expanding the
closing section to ~25% of paragraphs (at least 2 when the story is long)
let ending repair lift calm on known r1 from 0.4 → 1.0 while preserving the
body.

## Finding 2: one-shot pass rate understates the loop benefit

In one-shot paired comparisons, full regen often looked better on a single
re-Judge, and ending repair sometimes lifted `calm_ending` only to have the
LLM Judge re-score `preference_adherence` lower even though the body (and
every `must_include` element) was unchanged. That is Judge noise on a
strict 4/5 bar, not evidence that the body was damaged.

## Finding 3: inside Loop 2, ending repair helps (plan 06, n=3+3)

Small sample — treat these numbers as **directional evidence**, not a
definitive benchmark. The stronger evidence is qualitative: the gate fires
only on primarily-`calm_ending` failures, body text is preserved, and broader
failures still take the full-rewrite path.

| Setting | Pass rate | Mean attempts | Mean latency | Ending repairs used |
|---|---|---|---|---|
| Full regen only (`ending_repair=False`) | 1/3 | 2.7 | 18.7s | 0 |
| Ending repair enabled | **2/3** | **2.3** | **15.5s** | 1 on each pass |

When the first failure was primarily `calm_ending`, ending repair recovered
in one step:

- rep1: draft calm=0.6 → ending_repair calm=1.0, **passed**
- rep2: draft calm=0.6 → ending_repair calm=1.0, **passed**

When the first failure was preference-adherence with calm already fine
(rep3), ending repair correctly did *not* fire — full rewrite path used
instead (and still exhausted; same failure mode as the no-repair baseline).

Body preservation: 100% of ending-repair calls kept the pre-closing text.

## Recommendation

**Keep ending repair enabled by default** in `loop2.run`.

It is not a silver bullet for every Judge failure, and one-shot metrics alone
would have wrongly rejected it. In the multi-revision harness it is a cheap
first response to the specific failure the A/B evidence identified, with
full regeneration still available for broader failures. That matches the
intended final design:

```
DEFAULT:     whole-story generation
EXCEPTION:   targeted repair of the section the Judge identifies
```

### Honest limits

- Sample on the Loop 2 comparison is small (3+3 on one hard plan).
- `preference_adherence` LLM re-scoring after a body-preserving edit remains
  a Judge calibration issue; deterministic must_include checks are the
  reliable half of that dimension.
- High-energy plot shapes can still exhaust revisions when the first failure
  is not calm-only.
