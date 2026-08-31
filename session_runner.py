"""End-to-end: child request -> Loop 1 (approved plan) -> Loop 2 (final story).

REPORT.md sec 2: the two nested loops run back to back, sharing one
`SessionContext` and `UserPreferences` object, so a preference stated during
plan brainstorming (Loop 1) is still enforced during story writing (Loop 2)
-- e.g. `must_include` accumulated from plan feedback carries straight into
the Story Judge's `preference_adherence` check.
"""

from __future__ import annotations

from typing import Any, Callable

import loop1
import loop2
from llm import LLMClient
from models import SessionContext, StoryDraft, StoryPlan


def run_full_session(
    initial_request: str,
    index: list[dict[str, Any]],
    llm: LLMClient,
    respond_plan: Callable[[StoryPlan], str],
    respond_story: Callable[[StoryDraft], str],
    mock_fns: dict[str, Any] | None = None,
) -> SessionContext:
    session = loop1.run(initial_request, index, llm, respond=respond_plan, mock_fns=mock_fns)
    loop2.run(
        session.plan, session.preferences, llm,
        respond=respond_story, session=session, mock_fns=mock_fns,
    )
    return session
