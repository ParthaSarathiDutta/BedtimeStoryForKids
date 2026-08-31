"""Submission smoke tests: five scripted end-to-end sessions (real API).

Covers the three reading bands, a multi-round feedback path, and the hard
high-energy / calm-ending case. Child reactions are scripted (no live child);
the child-facing surface is exercised through the same present/respond shape
main.py uses, but without printing Judge scores.

Run: python smoke_e2e.py
Writes artifacts/smoke_e2e.md (gitignored summary for local QA).
"""

from __future__ import annotations

import json
import pathlib
import time

import llm as llm_module
import session_runner
from models import StoryDraft, StoryPlan

INDEX_PATH = pathlib.Path("corpus_index.json")
OUT_PATH = pathlib.Path("artifacts/smoke_e2e.md")

# (label, reading_band, request, plan replies, story replies)
SCENARIOS: list[tuple[str, str, str, list[str], list[str]]] = [
    (
        "5-6 age band",
        "5-6",
        "A cozy story about a little bunny who lost her favorite mitten in the snow.",
        ["yes!"],
        ["yes, I love it!"],
    ),
    (
        "7-8 age band",
        "7-8",
        "A story about a brave fox and an owl who find a secret treehouse.",
        ["yes please"],
        ["yes!"],
    ),
    (
        "9-10 age band",
        "9-10",
        "A story about two siblings sailing a canoe at dusk and meeting fireflies.",
        ["sounds wonderful"],
        ["perfect, thank you"],
    ),
    (
        "multi-round feedback",
        "7-8",
        "I want a story about a dragon.",
        [
            "can there be a spaceship too?",
            "yes, that sounds exciting!",
        ],
        [
            "can you make it a bit sillier?",
            "yes, that's perfect!",
        ],
    ),
    (
        "high-energy / calm-ending",
        "9-10",
        "A super silly story where one dropped sock starts the wildest chain of mix-ups ever, "
        "with a bicycle and lots of chaos, but it still has to be a bedtime story.",
        ["yes, go for it!"],
        ["the ending should be calmer", "yes, now it's good"],
    ),
]


def make_responder(scripted: list[str], label: str):
    remaining = list(scripted)

    def respond(artifact) -> str:
        reply = remaining.pop(0) if remaining else "yes!"
        if isinstance(artifact, StoryPlan):
            print(f"    [plan] {artifact.concept[:80]}...")
            if artifact.open_question:
                print(f"    [ask]  {artifact.open_question}")
        elif isinstance(artifact, StoryDraft):
            print(f"    [story] {len(artifact.text.split())} words, strategy={artifact.strategy}")
        print(f"    [child] {reply!r}")
        return reply

    return respond


def main() -> None:
    llm_module.load_env()
    index = json.loads(INDEX_PATH.read_text())["stories"]
    llm = llm_module.LLMClient(mock=False)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# End-to-end smoke tests\n"]
    results: list[dict] = []

    for i, (label, band, request, plan_script, story_script) in enumerate(SCENARIOS, 1):
        print(f"\n[{i}/{len(SCENARIOS)}] {label}: {request[:60]}...", flush=True)
        t0 = time.time()
        try:
            session = session_runner.run_full_session(
                request, index, llm,
                respond_plan=make_responder(plan_script, label),
                respond_story=make_responder(story_script, label),
                reading_band=band,
            )
            ok = session.story is not None and len(session.story.text.split()) > 50
            latency = time.time() - t0
            ending_repairs = sum(1 for e in session.trace if e.kind == "storyteller_ending_repair")
            story_verdicts = [e for e in session.trace if e.kind == "judge_story_verdict"]
            final_passed = story_verdicts[-1].payload["passed"] if story_verdicts else False
            fallback = any(
                e.kind in ("story_internal_revisions_exhausted", "plan_internal_revisions_exhausted")
                for e in session.trace
            )
            row = {
                "label": label, "ok": ok, "latency": round(latency, 1),
                "words": len(session.story.text.split()) if session.story else 0,
                "reading_band": session.preferences.known.get("reading_band"),
                "must_include": session.preferences.must_include,
                "final_judge_passed": final_passed,
                "fallback": fallback,
                "ending_repairs": ending_repairs,
                "strategy": session.story.strategy if session.story else None,
            }
            results.append(row)
            print(
                f"    -> ok={ok} passed={final_passed} words={row['words']} "
                f"repairs={ending_repairs} lat={latency:.1f}s",
                flush=True,
            )
            lines.append(f"\n## {i}. {label}\n")
            lines.append(f"- request: {request}")
            lines.append(f"- reading_band: {row['reading_band']}")
            lines.append(f"- must_include: {row['must_include']}")
            lines.append(f"- judge passed: {final_passed}, fallback: {fallback}")
            lines.append(f"- ending repairs: {ending_repairs}, latency: {latency:.1f}s")
            lines.append(f"- concept: {session.plan.concept}")
            lines.append(f"\n### Final story ({row['words']} words)\n")
            lines.append(session.story.text if session.story else "(none)")
            lines.append("")
        except Exception as exc:
            results.append({"label": label, "ok": False, "error": str(exc)})
            print(f"    -> FAILED: {exc}", flush=True)
            lines.append(f"\n## {i}. {label}\n\n**FAILED:** {exc}\n")

    passed = sum(1 for r in results if r.get("ok"))
    lines.insert(1, f"\n**Result: {passed}/{len(SCENARIOS)} scenarios produced a story.**\n")
    OUT_PATH.write_text("\n".join(lines))
    print(f"\n{passed}/{len(SCENARIOS)} ok — wrote {OUT_PATH}")
    if passed < len(SCENARIOS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
