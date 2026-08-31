"""Child-facing entry point for the bedtime storyteller.

What the child sees: a playful concept, at most one question, then the story.
What the child never sees: Judge scores, JSON, traces, revision counts, or
internal agent names. Those stay in SessionContext for debugging offline.

Before submitting the assignment, describe here in a few sentences what you
would have built next if you spent 2 more hours on this project:

I would harden Judge calibration on preference_adherence (the LLM half still
drifts after body-preserving edits even when deterministic must_include
checks pass), add a tiny "favorite stories" save/replay so a child can hear
last night's tale again without regenerating, and wire optional text-to-speech
for parents who want the story read aloud. None of those change the
architecture; they polish the product around the evidence-driven core.
"""

from __future__ import annotations

import json
import pathlib
import sys

import llm as llm_module
import session_runner
from llm import LLMClient, LLMError
from models import StoryDraft, StoryPlan

INDEX_PATH = pathlib.Path("corpus_index.json")

BANNER = """
Bedtime Story Time
------------------
Tell me what kind of story you'd like, and we'll make one together.
(You can say "yes" when you like an idea, or ask for changes anytime.)
"""


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def ask_reading_band() -> str | None:
    """Map a child's age to the taxonomy reading_band. Soft ask — skippable."""
    raw = _ask("How old are you? (or press Enter to skip) ")
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        lowered = raw.lower()
        for band in ("5-6", "7-8", "9-10"):
            if band in lowered:
                return band
        return None
    age = int(digits)
    if age <= 6:
        return "5-6"
    if age <= 8:
        return "7-8"
    return "9-10"


def present_plan(plan: StoryPlan) -> str:
    """Show only the playful concept and optional question — never internals."""
    print()
    print("Here's an idea for your story:")
    print()
    print(f"  {plan.concept}")
    print()
    if plan.open_question:
        print(f"  {plan.open_question}")
        print()
        return _ask("What do you think? ")
    return _ask("Does that sound good? (say yes, or tell me what to change) ")


def present_story(draft: StoryDraft) -> str:
    print()
    print("Okay — here is your story.")
    print()
    print(draft.text)
    print()
    return _ask("Did you like it? (say yes, or tell me what to change) ")


def load_index() -> list[dict]:
    if not INDEX_PATH.exists():
        print(
            "I can't find corpus_index.json. Build it first with the annotation "
            "pipeline (see README), or restore it from the repo.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    data = json.loads(INDEX_PATH.read_text())
    return data["stories"] if isinstance(data, dict) else data


def main() -> None:
    print(BANNER)
    llm_module.load_env()

    try:
        index = load_index()
        llm = LLMClient(mock=False)
    except SystemExit:
        raise
    except Exception:
        print(
            "I couldn't start up properly. Check that OPENAI_API_KEY is set in "
            ".env (see .env.example) and try again.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    reading_band = ask_reading_band()
    request = _ask("What kind of story do you want to hear? ")
    if not request:
        print("No story tonight? That's okay. Sweet dreams!")
        return

    print()
    print("Hmm, let me think of a good idea for you...")

    try:
        session = session_runner.run_full_session(
            request,
            index,
            llm,
            respond_plan=present_plan,
            respond_story=present_story,
            reading_band=reading_band,
        )
    except KeyboardInterrupt:
        print("\n\nOkay — we'll finish another night. Sweet dreams!")
        return
    except LLMError:
        print(
            "\nHmm, I got a bit stuck making that story. "
            "Let's try again in a moment — maybe with a simpler idea."
        )
        return
    except Exception:
        print(
            "\nSomething unexpected went wrong on my side. "
            "Sorry about that — please try again."
        )
        return

    if session.story is None:
        print("\nI couldn't finish a story this time. Let's try again soon.")
        return

    print()
    print("The end. Sweet dreams!")


if __name__ == "__main__":
    main()
