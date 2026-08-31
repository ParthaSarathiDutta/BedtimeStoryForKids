"""Helpers for durable explicit behavioral/thematic child asks."""

from __future__ import annotations

CONFLICT_ASK_SIGNALS = (
    "fight", "fighting", "rival", "rivals", "battle", "showdown", "versus", " vs ",
    "argue", "argument", "compete", "competition", "contest", "clash", "confront",
)

RIVALRY_CONCEPT_SIGNALS = CONFLICT_ASK_SIGNALS + (
    "face off", "challenge", "duel", "competitor", "opponent", "beat each other",
    "who is strongest", "who is mightiest", "prove who",
)

TEAMWORK_PIVOT_SIGNALS = (
    "team up", "work together", "cooperate", "cooperation", "help each other",
    "join forces", "save their home", "explore together", "friendship story",
    "best friends", "become friends",
)

HARM_SIGNALS = ("die", "death", "kill", "killed", "murder", "stab", "hurt badly", "severe harm")


def asks_text(preferences) -> str:
    return " ".join(getattr(preferences, "explicit_asks", []) or []).lower()


def concept_text(plan) -> str:
    return f"{plan.concept} {plan.protagonist} {plan.setting} {plan.plot_shape}".lower()


def wants_conflict(preferences) -> bool:
    return any(s in asks_text(preferences) for s in CONFLICT_ASK_SIGNALS)


def wants_harm(preferences) -> bool:
    text = asks_text(preferences)
    return any(s in text for s in HARM_SIGNALS)


def has_rivalry(concept: str) -> bool:
    return any(s in concept for s in RIVALRY_CONCEPT_SIGNALS)


def has_teamwork_pivot(concept: str) -> bool:
    return any(s in concept for s in TEAMWORK_PIVOT_SIGNALS)


def has_harm(concept: str) -> bool:
    return any(s in concept for s in HARM_SIGNALS)


def merge_explicit_asks(existing: list[str], new_items: list[str]) -> list[str]:
    out = list(existing)
    for item in new_items:
        cleaned = item.strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def remove_explicit_ask(existing: list[str], target: str) -> list[str]:
    t = target.strip().lower()
    return [a for a in existing if a.lower() != t]
