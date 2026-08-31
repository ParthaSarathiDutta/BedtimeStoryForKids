"""Scripted real-API smoke test for Loop 1 across several child requests.

Not a unit test -- `test_loop1.py` already checks wiring with mock responses.
This exists to answer the actual open question from the implementation-order
discussion: does gpt-3.5-turbo + this taxonomy + this Planner/Judge prompt
pair actually behave well together? Scripted rather than interactive so it is
reproducible and reviewable, since a live child is unavailable.

Run: python demo_loop1.py [--out artifacts/loop1_demo.md]
"""

from __future__ import annotations

import argparse
import json
import pathlib
from dataclasses import asdict

import llm as llm_module
import loop1
from models import StoryPlan

INDEX_PATH = pathlib.Path("corpus_index.json")

# (initial request, scripted child replies in order; loop1 asks again with
# "yes!" once the list is exhausted, so a scenario with one entry just tests
# immediate approval).
SCENARIOS: list[tuple[str, list[str]]] = [
    ("I want a story about a bunny who is scared of the dark.", ["yes!"]),
    ("A story about a dragon named Sparkle who loves cupcakes.", ["I love it!"]),
    ("make me a fun story", ["can you make it sillier?", "yes that's perfect"]),
    ("a story for my little sister who is 5, about going to school", ["yeah!"]),
    ("a spooky story about a haunted house", ["ok yes"]),
    ("a story about space", ["can there be a dog too?", "yes!"]),
    ("I dunno, surprise me!", ["sure, sounds good"]),
    ("story about friendship between a cat and a mouse in a garden", ["yes please"]),
]


def make_responder(scripted: list[str]):
    remaining = list(scripted)

    def respond(plan: StoryPlan) -> str:
        if remaining:
            return remaining.pop(0)
        return "yes!"

    return respond


def render_plan(plan: StoryPlan) -> str:
    lines = [
        f"- **concept**: {plan.concept}",
        f"- **protagonist**: {plan.protagonist}",
        f"- **setting**: {plan.setting}",
        f"- **plot_shape**: {plan.plot_shape}",
        f"- **arc_beats**: {' -> '.join(plan.arc_beats)}",
        f"- **open_question**: {plan.open_question or '(none)'}",
        f"- **inspiration_ids**: {plan.inspiration_ids or '(none)'}",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/loop1_demo.md")
    args = ap.parse_args()

    llm_module.load_env()
    if not INDEX_PATH.exists():
        raise SystemExit(f"{INDEX_PATH} not found -- run index_corpus.py first")
    index = json.loads(INDEX_PATH.read_text())["stories"]
    llm = llm_module.LLMClient(mock=False)

    out: list[str] = [f"# Loop 1 demo -- {len(SCENARIOS)} scripted scenarios, real gpt-3.5-turbo calls\n"]

    for i, (request, script) in enumerate(SCENARIOS, 1):
        print(f"[{i}/{len(SCENARIOS)}] {request!r}", flush=True)
        out.append(f"\n---\n\n## {i}. \"{request}\"\n")
        out.append(f"Scripted child replies: {script}\n")

        session = loop1.run(request, index, llm, respond=make_responder(script))

        out.append(f"\n**Extracted preferences:** `{session.preferences.known}`")
        out.append(f"\n**must_include:** `{session.preferences.must_include}`\n")

        drafts = [e for e in session.trace if e.kind == "planner_draft"]
        verdicts = [e for e in session.trace if e.kind == "judge_plan_verdict"]
        responses = [e for e in session.trace if e.kind == "child_response"]
        out.append(f"\n**Internal Planner/Judge cycles:** {len(drafts)} draft(s) across the whole session")
        out.append(f"**Child rounds:** {len(responses)}\n")

        for j, (d, v) in enumerate(zip(drafts, verdicts), 1):
            out.append(f"\n### Draft {j}")
            out.append(f"- concept: {d.payload['concept']}")
            out.append(f"- plot_shape: {d.payload['plot_shape']}")
            out.append(f"- open_question: {d.payload['open_question']}")
            out.append(f"- judge scores: {v.payload['scores']}")
            out.append(f"- judge passed: {v.payload['passed']}")
            if v.payload["deterministic_failures"]:
                out.append(f"- deterministic failures: {v.payload['deterministic_failures']}")
            out.append(f"- judge feedback: {v.payload['feedback']}")

        for r in responses:
            out.append(f"\n- child said: \"{r.payload['raw_text']}\" -> "
                       f"approved={r.payload['approved']}, intent={r.payload['intent']}, "
                       f"extracted_element={r.payload['extracted_element']}")

        out.append("\n\n**FINAL PLAN:**\n")
        out.append(render_plan(session.plan))

    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
