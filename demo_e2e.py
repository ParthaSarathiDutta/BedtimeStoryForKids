"""Real-API end-to-end smoke test: child request -> Loop 1 -> Loop 2 -> full story.

Whole-story strategy only (the baseline). Scripted, reproducible child
reactions for both loops, same rationale as demo_loop1.py: a live child is
unavailable, so scripted text stands in, reviewed by hand afterward.

Run: python demo_e2e.py
"""

from __future__ import annotations

import json
import pathlib

import llm as llm_module
import session_runner
from models import StoryDraft, StoryPlan

INDEX_PATH = pathlib.Path("corpus_index.json")

# (request, scripted plan-round replies, scripted story-round replies)
SCENARIOS: list[tuple[str, list[str], list[str]]] = [
    ("A story about a brave little mouse who is scared of thunderstorms.",
     ["yes!"], ["I love it! Yes."]),
    ("I want a story about a dragon and a spaceship.",
     ["yes, sounds fun!"], ["that's too exciting, can you make the ending calmer?", "yes, perfect"]),
    ("Tell me a story about a cat, a mouse, and a rabbit who are all friends.",
     ["yes please"], ["yes!"]),
]


def make_responder(scripted: list[str]):
    remaining = list(scripted)

    def respond(_artifact) -> str:
        return remaining.pop(0) if remaining else "yes!"

    return respond


def main() -> None:
    llm_module.load_env()
    index = json.loads(INDEX_PATH.read_text())["stories"]
    llm = llm_module.LLMClient(mock=False)

    out: list[str] = ["# End-to-end demo -- Loop 1 -> Loop 2, whole-story strategy\n"]

    for i, (request, plan_script, story_script) in enumerate(SCENARIOS, 1):
        print(f"[{i}/{len(SCENARIOS)}] {request!r}", flush=True)
        session = session_runner.run_full_session(
            request, index, llm,
            respond_plan=make_responder(plan_script),
            respond_story=make_responder(story_script),
        )

        out.append(f"\n---\n\n## {i}. \"{request}\"\n")
        out.append(f"**must_include (final):** `{session.preferences.must_include}`\n")
        out.append(f"\n**APPROVED CONCEPT:** {session.plan.concept}")
        out.append(f"\n**Protagonist/Setting:** {session.plan.protagonist} / {session.plan.setting}\n")

        story_drafts = [e for e in session.trace if e.kind == "storyteller_draft"]
        story_verdicts = [e for e in session.trace if e.kind == "judge_story_verdict"]
        out.append(f"\n**Story drafts:** {len(story_drafts)}")
        for j, v in enumerate(story_verdicts, 1):
            out.append(f"\n{j}. scores: {v.payload['scores']}  passed: {v.payload['passed']}")
            if v.payload["deterministic_failures"]:
                out.append(f"   deterministic failures: {v.payload['deterministic_failures']}")
            if not v.payload["passed"]:
                out.append(f"   feedback: {v.payload['feedback']}")

        story_responses = [e for e in session.trace if e.kind == "child_story_response"]
        for r in story_responses:
            out.append(f"\n- child said: \"{r.payload['raw_text']}\" -> approved={r.payload['approved']}")

        word_count = len(session.story.text.split())
        out.append(f"\n\n**FINAL STORY** ({word_count} words):\n")
        out.append(session.story.text)

    path = pathlib.Path("artifacts/e2e_demo.md")
    path.write_text("\n".join(out))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
