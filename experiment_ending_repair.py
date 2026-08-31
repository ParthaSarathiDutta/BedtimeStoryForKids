"""Narrow comparison: full-story regeneration vs. targeted ending repair
when the Story Judge fails primarily on calm_ending.

Paired on the *same* failed draft whenever possible:

    draft0 = write_story(plan)
    verdict0 = evaluate_story(draft0)
    if primarily calm_ending failure:
        full  = write_story(plan, notes=feedback)     # current behavior
        end   = revise_ending(draft0, feedback)         # new behavior
        re-judge both

Also repairs the known-failed whole-story drafts from the A/B experiment
(plan 06 silly/cumulative, both repeats) as a free offline check against
the exact failure cases that motivated this path.

Usage: python experiment_ending_repair.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import judge
import storyteller
from experiment_strategies import FIXED_PLANS
from llm import LLMClient, load_env
from models import StoryDraft, UserPreferences

load_env()

ARTIFACT_DIR = Path("artifacts/ending_repair")
STORIES_DIR = ARTIFACT_DIR / "stories"
LIVE_ATTEMPTS_PER_PLAN = 3

# Plans most likely to hit calm_ending failure, plus one ordinary control.
FOCUS_PLAN_IDS = (
    "06_silly_cumulative_9-10",
    "02_quest_rescue_7-8",
    "05_overcome_challenge_7-8",
    "08_quest_rescue_9-10",
)


def _prefs_for(plan_id: str) -> tuple[StoryPlan, UserPreferences]:
    for pid, plan, prefs in FIXED_PLANS:
        if pid == plan_id:
            return plan, UserPreferences(
                initial_request=prefs.initial_request,
                must_include=list(prefs.must_include),
            )
    raise KeyError(plan_id)


def _score_summary(verdict) -> dict:
    return {
        "passed": verdict.passed,
        "scores": dict(verdict.scores),
        "deterministic_failures": list(verdict.deterministic_failures),
        "feedback": verdict.feedback,
        "calm_ending": verdict.scores.get("calm_ending"),
        "preference_adherence": verdict.scores.get("preference_adherence"),
        "arc_coherence": verdict.scores.get("arc_coherence"),
    }


def _body_prefix(text: str, n: int = 80) -> str:
    body, _ = storyteller.split_body_and_ending(text)
    return body[:n]


def repair_known_failures(llm: LLMClient) -> list[dict]:
    """Offline: take the A/B whole-story failures for plan 06 and repair endings."""
    rows = []
    ab_dir = Path("artifacts/ab_experiment/stories")
    plan, prefs = _prefs_for("06_silly_cumulative_9-10")
    for repeat in (1, 2):
        path = ab_dir / f"06_silly_cumulative_9-10__whole_story__r{repeat}.txt"
        if not path.exists():
            continue
        text = path.read_text()
        draft0 = StoryDraft(text=text, plan=plan, strategy=storyteller.STRATEGY_WHOLE_STORY)
        v0 = judge.evaluate_story(draft0, prefs, llm)
        print(f"  known r{repeat}: passed={v0.passed} calm={v0.scores.get('calm_ending')} "
              f"primarily_calm={judge.is_primarily_calm_ending_failure(v0)}", flush=True)

        t0 = time.time()
        repaired = storyteller.revise_ending(draft0, prefs, llm, judge_feedback=v0.feedback)
        v1 = judge.evaluate_story(repaired, prefs, llm)
        latency = time.time() - t0

        STORIES_DIR.mkdir(parents=True, exist_ok=True)
        out = STORIES_DIR / f"known_06_r{repeat}__ending_repair.txt"
        out.write_text(repaired.text)

        rows.append({
            "kind": "known_failure_repair",
            "source": str(path),
            "before": _score_summary(v0),
            "after": _score_summary(v1),
            "primarily_calm_before": judge.is_primarily_calm_ending_failure(v0),
            "body_preserved": repaired.text.startswith(_body_prefix(text, 40)),
            "latency_seconds": round(latency, 2),
            "llm_calls": 2,  # ending + re-judge (initial judge already counted separately)
            "story_file": str(out),
            "word_count_before": len(text.split()),
            "word_count_after": len(repaired.text.split()),
        })
        print(f"    -> after ending repair: passed={v1.passed} calm={v1.scores.get('calm_ending')} "
              f"pref={v1.scores.get('preference_adherence')} coh={v1.scores.get('arc_coherence')}",
              flush=True)
    return rows


def live_paired(llm: LLMClient) -> list[dict]:
    """Generate fresh drafts; when calm_ending-only fails, apply both repairs."""
    rows = []
    for plan_id in FOCUS_PLAN_IDS:
        plan, prefs = _prefs_for(plan_id)
        for attempt in range(1, LIVE_ATTEMPTS_PER_PLAN + 1):
            print(f"  live {plan_id} attempt {attempt} ...", flush=True)
            t_gen = time.time()
            draft0 = storyteller.write_story(plan, prefs, llm)
            v0 = judge.evaluate_story(draft0, prefs, llm)
            gen_latency = time.time() - t_gen
            print(f"    draft0: passed={v0.passed} calm={v0.scores.get('calm_ending')} "
                  f"primarily_calm={judge.is_primarily_calm_ending_failure(v0)}", flush=True)

            base = {
                "kind": "live_paired",
                "plan_id": plan_id,
                "attempt": attempt,
                "draft0": _score_summary(v0),
                "primarily_calm_failure": judge.is_primarily_calm_ending_failure(v0),
                "gen_latency_seconds": round(gen_latency, 2),
                "word_count_draft0": len(draft0.text.split()),
            }

            if v0.passed:
                rows.append({**base, "outcome": "passed_first_try"})
                continue

            if not judge.is_primarily_calm_ending_failure(v0):
                rows.append({**base, "outcome": "failed_but_not_calm_only"})
                continue

            # Paired repairs on the same failed draft
            t_full = time.time()
            full = storyteller.write_story(plan, prefs, llm, revision_notes=v0.feedback)
            v_full = judge.evaluate_story(full, prefs, llm)
            full_latency = time.time() - t_full

            t_end = time.time()
            end = storyteller.revise_ending(draft0, prefs, llm, judge_feedback=v0.feedback)
            v_end = judge.evaluate_story(end, prefs, llm)
            end_latency = time.time() - t_end

            STORIES_DIR.mkdir(parents=True, exist_ok=True)
            p0 = STORIES_DIR / f"{plan_id}__a{attempt}__draft0.txt"
            pf = STORIES_DIR / f"{plan_id}__a{attempt}__full_regen.txt"
            pe = STORIES_DIR / f"{plan_id}__a{attempt}__ending_repair.txt"
            p0.write_text(draft0.text)
            pf.write_text(full.text)
            pe.write_text(end.text)

            row = {
                **base,
                "outcome": "paired_repair",
                "full_regen": {
                    **_score_summary(v_full),
                    "latency_seconds": round(full_latency, 2),
                    "llm_calls": 2,
                    "word_count": len(full.text.split()),
                    "story_file": str(pf),
                },
                "ending_repair": {
                    **_score_summary(v_end),
                    "latency_seconds": round(end_latency, 2),
                    "llm_calls": 2,
                    "word_count": len(end.text.split()),
                    "body_preserved": end.text.startswith(_body_prefix(draft0.text, 40)),
                    "story_file": str(pe),
                },
                "draft0_file": str(p0),
            }
            rows.append(row)
            print(
                f"    full_regen: passed={v_full.passed} calm={v_full.scores.get('calm_ending')} "
                f"lat={full_latency:.1f}s | ending_repair: passed={v_end.passed} "
                f"calm={v_end.scores.get('calm_ending')} lat={end_latency:.1f}s "
                f"body_ok={row['ending_repair']['body_preserved']}",
                flush=True,
            )
    return rows


def summarize(rows: list[dict]) -> str:
    lines = ["# Ending-repair experiment summary", ""]

    known = [r for r in rows if r["kind"] == "known_failure_repair"]
    if known:
        lines.append("## Offline repair of A/B plan-06 whole-story failures")
        for r in known:
            b, a = r["before"], r["after"]
            lines.append(
                f"- {r['source']}: calm {b['calm_ending']}→{a['calm_ending']}, "
                f"passed {b['passed']}→{a['passed']}, "
                f"pref {b['preference_adherence']}→{a['preference_adherence']}, "
                f"coh {b['arc_coherence']}→{a['arc_coherence']}, "
                f"body_preserved={r['body_preserved']}, latency={r['latency_seconds']}s"
            )
        lines.append("")

    paired = [r for r in rows if r.get("outcome") == "paired_repair"]
    first_pass = sum(1 for r in rows if r.get("outcome") == "passed_first_try")
    other_fail = sum(1 for r in rows if r.get("outcome") == "failed_but_not_calm_only")
    lines.append("## Live paired runs")
    lines.append(f"- first-try passes: {first_pass}")
    lines.append(f"- failed but not calm-only: {other_fail}")
    lines.append(f"- paired calm-ending repairs: {len(paired)}")
    if paired:
        full_ok = sum(1 for r in paired if r["full_regen"]["passed"])
        end_ok = sum(1 for r in paired if r["ending_repair"]["passed"])
        full_calm = sum(r["full_regen"]["calm_ending"] or 0 for r in paired) / len(paired)
        end_calm = sum(r["ending_repair"]["calm_ending"] or 0 for r in paired) / len(paired)
        full_lat = sum(r["full_regen"]["latency_seconds"] for r in paired) / len(paired)
        end_lat = sum(r["ending_repair"]["latency_seconds"] for r in paired) / len(paired)
        body_ok = sum(1 for r in paired if r["ending_repair"]["body_preserved"])
        lines.append(f"- full-regen recovery: {full_ok}/{len(paired)}")
        lines.append(f"- ending-repair recovery: {end_ok}/{len(paired)}")
        lines.append(f"- mean calm_ending after full-regen: {full_calm:.2f}")
        lines.append(f"- mean calm_ending after ending-repair: {end_calm:.2f}")
        lines.append(f"- mean repair latency full-regen: {full_lat:.1f}s")
        lines.append(f"- mean repair latency ending-repair: {end_lat:.1f}s")
        lines.append(f"- body preserved after ending-repair: {body_ok}/{len(paired)}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    llm = LLMClient(mock=False)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    print("=== Offline: repair known A/B failures ===", flush=True)
    rows.extend(repair_known_failures(llm))

    print("=== Live: paired full-regen vs ending-repair ===", flush=True)
    rows.extend(live_paired(llm))

    with open(ARTIFACT_DIR / "results.json", "w") as f:
        json.dump(rows, f, indent=2)
    summary = summarize(rows)
    (ARTIFACT_DIR / "report.md").write_text(summary + "\nSee results.json and stories/ for detail.\n")
    print("\n" + summary)
    print(f"Wrote {ARTIFACT_DIR / 'results.json'}")


if __name__ == "__main__":
    main()
