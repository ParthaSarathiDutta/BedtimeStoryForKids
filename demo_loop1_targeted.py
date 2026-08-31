"""Targeted real-API validation of the two fixes from the first demo run:
must_include vs interest_tags sorting, and element replacement.

Not general-purpose exploration (that was demo_loop1.py) -- each scenario
here is aimed at one specific failure mode already observed or explicitly
worth ruling out before generalizing Loop 1 into a shared harness.

Run: python demo_loop1_targeted.py
"""

from __future__ import annotations

import json
import pathlib

import llm as llm_module
import loop1
from models import StoryPlan

INDEX_PATH = pathlib.Path("corpus_index.json")

SCENARIOS: list[tuple[str, list[str]]] = [
    # 1. Multiple entities: all three must survive.
    ("Tell me a story about a cat, a mouse, and a rabbit.", ["yes!"]),
    # 2. Incremental addition across two feedback rounds: both must persist.
    ("I want a space story.", ["Add a dog!", "And a robot!", "perfect, yes"]),
    # 3. Replacement: the dragon must disappear, the dinosaur must persist.
    ("I want a story about a dragon.", ["No dragon, make it a dinosaur instead.", "yes!"]),
    # 4. Abstract preference must NOT become a literal must_include entry.
    ("Make it funnier please.", ["yes that's great"]),
    # 5. Named characters + explicit setting, the original failing case.
    ("A mouse and cat who are friends in a garden.", ["yes please"]),
]


def make_responder(scripted: list[str]):
    remaining = list(scripted)

    def respond(plan: StoryPlan) -> str:
        return remaining.pop(0) if remaining else "yes!"

    return respond


def main() -> None:
    llm_module.load_env()
    index = json.loads(INDEX_PATH.read_text())["stories"]
    llm = llm_module.LLMClient(mock=False)

    out: list[str] = ["# Loop 1 targeted validation -- 5 scenarios, real gpt-3.5-turbo\n"]

    for i, (request, script) in enumerate(SCENARIOS, 1):
        print(f"[{i}/{len(SCENARIOS)}] {request!r}", flush=True)
        session = loop1.run(request, index, llm, respond=make_responder(script))

        out.append(f"\n---\n\n## {i}. \"{request}\"\n")
        out.append(f"Scripted replies: {script}\n")
        out.append(f"\n**Extracted preferences:** `{session.preferences.known}`")
        out.append(f"\n**must_include (final):** `{session.preferences.must_include}`")

        for r in session.preferences.plan_feedback:
            out.append(
                f"\n- child said: \"{r.raw_text}\" -> approved={r.approved}, intent={r.intent}, "
                f"extracted_element={r.extracted_element}, removed_element={r.removed_element}"
            )

        drafts = [e for e in session.trace if e.kind == "planner_draft"]
        verdicts = [e for e in session.trace if e.kind == "judge_plan_verdict"]
        out.append(f"\n\n**{len(drafts)} internal draft(s):**")
        for j, (d, v) in enumerate(zip(drafts, verdicts), 1):
            out.append(f"\n{j}. concept: {d.payload['concept']}")
            out.append(f"   scores: {v.payload['scores']}  passed: {v.payload['passed']}")
            if v.payload["deterministic_failures"]:
                out.append(f"   deterministic failures: {v.payload['deterministic_failures']}")

        out.append(f"\n\n**FINAL CONCEPT:** {session.plan.concept}")
        out.append(f"\n**FINAL PROTAGONIST/SETTING:** {session.plan.protagonist} / {session.plan.setting}")

    path = pathlib.Path("artifacts/loop1_targeted_demo.md")
    path.write_text("\n".join(out))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
