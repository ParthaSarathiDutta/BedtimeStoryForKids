"""Generic Agent<->Judge<->child revision loop (REPORT.md sec 6).

Loop 1 (`loop1.py`) was deliberately run as a one-off, explicit driver until
its actual shape under gpt-3.5-turbo was validated against real API calls --
generalizing earlier risked freezing an interface around assumptions that
later needed to change (both the approval boolean and the must_include /
interest_tags split needed revision after real runs; see loop1.py and
preference_extractor.py). This is what survived that validation: `agent_fn`
and `judge_fn` are the only pieces that see domain types (`StoryPlan`,
`JudgeResult`, ...); `AgentLoop` itself needs only `passed`/`feedback` off
whatever `judge_fn` returns, and a plain `(approved, revision_notes)` pair
from `collect_fn`.
"""

from __future__ import annotations

from typing import Callable, Generic, Protocol, TypeVar

import config
from models import SessionContext

Artifact = TypeVar("Artifact")


class Verdict(Protocol):
    passed: bool
    feedback: str


class AgentLoop(Generic[Artifact]):
    """Runs an internal Agent<->Judge cycle (invisible to the child), then
    presents the result and collects a reaction, repeating until approved or
    a round cap is hit. Both nested loops in REPORT.md sec 2.2 are meant to be
    one call to this with different functions plugged in.
    """

    def __init__(
        self,
        agent_fn: Callable[[Artifact | None, str | None], Artifact],
        judge_fn: Callable[[Artifact], Verdict],
        present_fn: Callable[[Artifact], None],
        collect_fn: Callable[[Artifact], tuple[bool, str | None]],
        session: SessionContext,
        max_internal: int = config.MAX_INTERNAL_REVISIONS,
        max_child_rounds: int = config.MAX_CHILD_ROUNDS,
        trace_kind: str = "loop",
    ) -> None:
        self.agent_fn = agent_fn
        self.judge_fn = judge_fn
        self.present_fn = present_fn
        self.collect_fn = collect_fn
        self.session = session
        self.max_internal = max_internal
        self.max_child_rounds = max_child_rounds
        self.trace_kind = trace_kind

    def _internal_cycle(self, artifact: Artifact | None, notes: str | None) -> Artifact:
        """Agent<->Judge revision, invisible to the child. Graceful
        degradation (REPORT.md sec 6.3): the last draft stands even if the
        Judge never fully passes, rather than erroring out on the child.
        """
        for _ in range(self.max_internal):
            artifact = self.agent_fn(artifact, notes)
            verdict = self.judge_fn(artifact)
            if verdict.passed:
                return artifact
            notes = verdict.feedback
        self.session.log(f"{self.trace_kind}_internal_revisions_exhausted")
        return artifact  # type: ignore[return-value]

    def run(self) -> Artifact:
        artifact: Artifact | None = None
        notes: str | None = None
        child_rounds = 0

        while True:
            artifact = self._internal_cycle(artifact, notes)
            self.present_fn(artifact)
            approved, notes = self.collect_fn(artifact)
            if approved:
                return artifact

            child_rounds += 1
            if child_rounds >= self.max_child_rounds:
                self.session.log(f"{self.trace_kind}_child_rounds_exhausted")
                return artifact  # best-effort fallback, never an error to the child
