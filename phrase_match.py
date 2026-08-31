"""Deterministic required-phrase matching for Judge checks.

Handles harmless lexical formatting differences (spaces, hyphens) while
keeping word-boundary safety (\"cat\" must not match inside \"caterpillar\").
"""

from __future__ import annotations

import re

_LEADING_ARTICLES = ("a ", "an ", "the ")


def core_phrase(phrase: str) -> str:
    p = phrase.strip().lower()
    for article in _LEADING_ARTICLES:
        if p.startswith(article):
            return p[len(article):]
    return p


def _alnum_compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def mentions_required_phrase(phrase: str, haystack: str) -> bool:
    """Return True if `phrase` is reflected in `haystack`.

    Examples that match:
    - bluewhale ↔ blue whale
    - fire-fly ↔ firefly

    Examples that do not:
    - cat ↔ caterpillar
    """
    core = core_phrase(phrase)
    if not core:
        return True

    h = haystack.lower()

    # Full phrase as word-bounded tokens (handles "blue whale").
    if re.search(rf"\b{re.escape(core)}\b", h):
        return True

    tokens = [t for t in re.split(r"[\s\-]+", core) if t]
    if len(tokens) > 1 and all(re.search(rf"\b{re.escape(t)}\b", h) for t in tokens):
        return True

    compact_core = _alnum_compact(core)
    compact_hay = _alnum_compact(haystack)
    if len(compact_core) >= 4 and compact_core in compact_hay:
        return True

    if len(tokens) == 1:
        return re.search(rf"\b{re.escape(tokens[0])}\b", h) is not None

    return False
