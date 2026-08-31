"""Turns a child's free-text reaction into a `ChildResponse` (REPORT.md sec 5.3).

Children this age answer with "yes!", "make it funnier", or "I want a
dragon!" -- not literal, actionable instructions. No fixed keyword-rule set
holds up against real child phrasing, so this is a small LLM call, kept cheap:
one short prompt, temperature 0, tiny output.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from llm import LLMClient
from models import ChildResponse

_VALID_INTENTS = frozenset({
    "approve", "new_idea", "more_fun", "too_long", "too_scary", "add_element", "other",
})

# Code-side safety net: gpt-3.5-turbo has labeled clear revision requests
# ("the ending should be calmer", "can you make it sillier?") as approve.
# If any of these cues appear, we refuse to treat the turn as approval even
# when the model says intent=approve.
_REVISION_CUES = re.compile(
    r"(?i)\b("
    r"can (there|you|we)|could you|make (it|the)|should be|"
    r"too (long|scary|exciting|much|wild)|"
    r"calmer|sillier|funnier|scarier|shorter|longer|"
    r"add |instead|change |don'?t |no more|not so|please (fix|change)"
    r")\b"
)

PROMPT_TEMPLATE = """A child aged 5-10 was just asked about a bedtime story idea and said:

"{raw_text}"

Classify their reaction. Guidance:
- Pure acceptance with no change request ("yes", "yeah!", "sounds good", "I like it",
  "perfect") -> approved: true, intent: "approve"
- ANY request to change the story or ending is NOT approval, even if polite or
  soft. Examples that must be approved: false:
  "the ending should be calmer", "can you make it sillier?", "a bit too exciting",
  "make it shorter", "can there be a dog too?"
- A specific new request naming a concrete thing ("I want a dragon!", "can there be a dog too?")
  -> approved: false, intent: "new_idea" or "add_element", extracted_element set to that concrete thing
- Swapping one concrete thing for another ("no dragon, make it a dinosaur instead")
  -> intent: "add_element", extracted_element: "a dinosaur", removed_element: "a dragon"
- "make it funnier/sillier" -> intent: "more_fun". This is a MOOD, not a concrete
  thing -- extracted_element MUST be null here, never a word like "funny".
- "make it calmer" / "the ending should be calmer" / "too exciting" -> intent: "other",
  approved: false, extracted_element null
- "that's too long" / "too much" -> intent: "too_long", extracted_element null
- "that's scary" / "I don't like scary things" -> intent: "too_scary", extracted_element null
- Anything else -> intent: "other", approved false unless clearly positive with no change

extracted_element and removed_element are ONLY for concrete nameable things
(a character, an animal, an object, a specific place) -- never a mood, tone,
or abstract quality like "funny", "exciting", "calmer", or "friendship".

Reply with ONLY a JSON object:
{{
  "approved": true or false,
  "intent": one of "approve", "new_idea", "more_fun", "too_long", "too_scary", "add_element", "other",
  "extracted_element": "a short phrase for a concrete thing newly requested, e.g. 'a dog', or null",
  "removed_element": "a short phrase for a concrete thing explicitly retracted, e.g. 'a dragon', or null"
}}
"""


def build_prompt(raw_text: str) -> str:
    return PROMPT_TEMPLATE.format(raw_text=raw_text.strip())


def _validate(parsed: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not isinstance(parsed.get("approved"), bool):
        problems.append("approved: must be boolean")
    if parsed.get("intent") not in _VALID_INTENTS:
        problems.append(f"intent: {parsed.get('intent')!r} not recognized")
    for key in ("extracted_element", "removed_element"):
        if key in parsed and parsed[key] is not None and not isinstance(parsed[key], str):
            problems.append(f"{key}: must be a string or null")
    return problems


def interpret(
    raw_text: str,
    llm: LLMClient,
    mock_fn: Callable[[str], dict[str, Any]] | None = None,
) -> ChildResponse:
    prompt = build_prompt(raw_text)
    parsed = llm.complete_json(
        prompt,
        temperature=0.0,
        max_tokens=150,
        validate=_validate,
        mock_fn=mock_fn,
    )
    intent = parsed["intent"]
    element = parsed.get("extracted_element")
    element = str(element).strip() if element else None
    removed = parsed.get("removed_element")
    removed = str(removed).strip() if removed else None

    # `approved` is derived, not trusted from the model's own boolean. A real
    # run returned approved=true, intent="approve" for "can there be a dog
    # too?" while simultaneously extracting "a dog" as a new element in the
    # same response -- an internally contradictory answer that silently
    # dropped the child's request (the loop ended before the dog was ever
    # added). Same failure mode as the metadata pipeline's unreliable safety
    # flags: don't let an LLM's self-reported boolean gate a decision when
    # code can derive a safer one from the rest of its own output.
    approved = (intent == "approve") and element is None

    # Second safety net: revision cues in the raw text override a mistaken
    # approve intent (seen live on "the ending should be calmer").
    if approved and _REVISION_CUES.search(raw_text or ""):
        approved = False
        if intent == "approve":
            intent = "other"

    return ChildResponse(
        raw_text=raw_text,
        approved=approved,
        intent=intent,
        extracted_element=element,
        removed_element=removed,
    )
