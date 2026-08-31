"""Shared runtime types for the storytelling pipeline.

Single definitions used by the preference extractor, Planner, and Judge (and,
once built, the Storyteller), so a disagreement between agents about what a
"plan" or a "preference" looks like is a type error, not a silent mismatch
discovered at runtime.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


@dataclass
class ChildResponse:
    """A child's free-text reaction, interpreted into something an agent can act on.

    See `feedback_normalizer.py`. `intent` and `extracted_element` are
    best-effort -- classifying five-year-old enthusiasm is inherently
    approximate -- so agents should treat `raw_text` as ground truth and the
    rest as a hint.
    """
    raw_text: str
    approved: bool
    intent: Literal[
        "approve", "new_idea", "more_fun", "too_long", "too_scary",
        "add_element", "other",
    ]
    extracted_element: str | None = None   # concrete entity newly requested, e.g. "a dog"
    removed_element: str | None = None     # concrete entity explicitly retracted, e.g. "a dragon"
    explicit_ask: str | None = None        # behavioral/thematic ask, e.g. "they should fight"
    removed_explicit_ask: str | None = None


@dataclass
class UserPreferences:
    """Everything known about what this child wants, accumulated across both loops.

    Passed to the Planner, the Storyteller, and the Judge (REPORT.md sec 5.4)
    so a preference stated in round 1 cannot be silently dropped by a revision
    in round 3 without the Judge catching it.
    """
    initial_request: str
    known: dict[str, Any] = field(default_factory=dict)   # schema field name -> value(s)
    must_include: list[str] = field(default_factory=list)  # e.g. "dragon" -- checked every round
    explicit_asks: list[str] = field(default_factory=list)  # behavioral/thematic asks, e.g. "fighting"
    plan_feedback: list[ChildResponse] = field(default_factory=list)
    story_feedback: list[ChildResponse] = field(default_factory=list)

    def _apply(self, response: ChildResponse) -> None:
        """Removal happens before addition, so "no dragon, make it a dinosaur"
        in one sentence correctly ends with only the dinosaur required."""
        if response.removed_element:
            target = response.removed_element.lower()
            self.must_include = [m for m in self.must_include if m.lower() != target]
        if response.extracted_element and response.extracted_element not in self.must_include:
            self.must_include.append(response.extracted_element)
        if response.removed_explicit_ask:
            target = response.removed_explicit_ask.lower()
            self.explicit_asks = [a for a in self.explicit_asks if a.lower() != target]
        if response.explicit_ask and response.explicit_ask not in self.explicit_asks:
            self.explicit_asks.append(response.explicit_ask)

    def record_plan_feedback(self, response: ChildResponse) -> None:
        self.plan_feedback.append(response)
        self._apply(response)

    def record_story_feedback(self, response: ChildResponse) -> None:
        self.story_feedback.append(response)
        self._apply(response)


@dataclass
class InspirationCard:
    """Compact, structure-only view of one retrieved corpus story.

    Deliberately excludes story prose. The Planner gets a summary and metadata
    -- enough to inform structure -- never the published text, which would be
    both a copying risk and unnecessary prompt size.
    """
    story_id: str
    title: str
    summary: str
    matched_metadata: dict[str, Any]
    plot_shape: str
    narrative_style: list[str]
    tone: list[str]


@dataclass
class StoryPlan:
    """The Planner's proposed concept for Loop 1: a pitch, not a story.

    `arc_beats` is guidance carried forward for the Storyteller (REPORT.md
    sec 3): the Planner selects the profile via `plot_shape`; the Storyteller
    decides how literally to follow it.

    `child_notice` is an optional 1-2 sentence message when the Planner had to
    adapt an explicit child request for bedtime safety or age/bedtime constraints
    (transparent constraint adaptation). Null for ordinary revisions. Shown to the
    child; never contains policy/moderation jargon.
    """
    concept: str                    # 1-3 sentence pitch, shown to the child
    protagonist: str
    setting: str
    plot_shape: str
    arc_beats: list[str]
    metadata: dict[str, Any]        # resolved search_metadata-shaped preferences
    open_question: str | None       # next question for the child, or None
    inspiration_ids: list[str] = field(default_factory=list)  # traceability only
    child_notice: str | None = None

    def revise(self, **changes: Any) -> "StoryPlan":
        """Plans are immutable so the trace log can hold every draft without aliasing."""
        return dataclasses.replace(self, **changes)


@dataclass
class StoryDraft:
    """Full prose for an approved plan (REPORT.md sec 2, sec 3).

    Carries the plan it was written from, not just its own text: the Story
    Judge treats the approved plan as a contract (per the sequencing
    discussion before Loop 2 was built) and needs it to check adherence.
    """
    text: str
    plan: StoryPlan
    strategy: str  # e.g. "whole_story"; see storyteller.py


@dataclass
class JudgeResult:
    """Verdict from the Judge on a plan (or, later, a story draft).

    Pass/fail is decided in code from `passed`, never inferred from `scores`
    alone (REPORT.md sec 6.1): the Judge reports both the deterministic check
    outcomes and the LLM-scored dimensions, and a caller-side rule combines
    them, so the decision rule is visible and testable rather than hidden
    inside a model's opinion of its own verdict.
    """
    passed: bool
    scores: dict[str, float]            # LLM-scored dimensions, normalized to [0, 1]
    reasons: dict[str, str]             # one short justification per scored dimension
    deterministic_failures: list[str]   # code-checked requirements that failed
    feedback: str                       # actionable text for the agent to revise against


@dataclass
class TraceEvent:
    kind: str
    payload: dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


@dataclass
class SessionContext:
    """Single source of truth for one child's session (REPORT.md sec 2).

    Minimal by design during the Loop 1 slice: preferences, the current plan,
    and a trace log. Loop-control (internal revision counts, child round
    counts) stays in the loop driver rather than here until a second loop
    exists to show which parts of that control flow are genuinely shared.
    """
    preferences: UserPreferences
    plan: StoryPlan | None = None
    story: StoryDraft | None = None
    trace: list[TraceEvent] = field(default_factory=list)

    def log(self, kind: str, **payload: Any) -> None:
        self.trace.append(TraceEvent(kind=kind, payload=payload))
