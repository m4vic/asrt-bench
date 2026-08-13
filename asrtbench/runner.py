"""Fire a pack at a target and collect per-attack verdicts.

This is the engine behind `/attack` (the CLI is a later increment). It loads a
pack, mints a per-case canary, runs each attack through the harness against the
selected target, and verifies deterministically. `unclear` is kept as its own
outcome -- never folded into a pass rate, never counted as pass or fail.

Every tool the harness lends is inert: nothing is emailed, written, queried, or
executed for real. So there is no live/dry-run distinction to make yet -- a run
against the current target model can have no real side effect by construction.
That safety knob becomes meaningful only if asrt-bench ever drives a target's
OWN (real) tools, which it does not today.
"""

from __future__ import annotations

import asyncio
import glob
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from asrtbench.adjudication import Verifier
from asrtbench.core import AttackCase, Suite
from asrtbench.harness import ActionHarness
from asrtbench.target import Target


def load_pack(pack_dir: str) -> list[AttackCase]:
    """Load every action-channel attack from a pack directory."""
    cases: list[AttackCase] = []
    for path in sorted(glob.glob(os.path.join(pack_dir, "*.json"))):
        cases.extend(c for c in AttackCase.load_file(path) if c.channel == "action")
    return cases


def starter_pack_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "packs", "starter")


@dataclass
class CaseOutcome:
    attack_id: str
    category: str
    verdict: str                     # "success" | "failure" | "unclear"
    reason: str
    evidence_seq: list[int]
    blast_radius: dict[str, Any] = field(default_factory=dict)
    tool_calls: int = 0


@dataclass
class RunResult:
    run_id: str
    target: str
    pack_hash: str                   # the pack's content hash -- the diff gate reads this
    outcomes: list[CaseOutcome] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c = {"success": 0, "failure": 0, "unclear": 0, "total": 0}
        for o in self.outcomes:
            c[o.verdict] = c.get(o.verdict, 0) + 1
            c["total"] += 1
        return c


def _canary(run_id: str, case: AttackCase) -> str:
    return f"ASRT-CANARY-{run_id}-{case.attack_id.replace(':', '-')}"


def run_pack(
    target: Target,
    pack_dir: str | None = None,
    *,
    max_tool_calls: int = 6,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> RunResult:
    pack_dir = pack_dir or starter_pack_dir()
    cases = load_pack(pack_dir)
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    # Content hash of the exact attacks fired -- a diff between two runs is only
    # valid if this matches (the pack-identity gate).
    suite = Suite.freeze(os.path.basename(pack_dir.rstrip("/\\")) or "pack", cases)
    result = RunResult(run_id=run_id, target=target.name, pack_hash=suite.suite_hash)

    def _emit(stage: str, **p: Any) -> None:
        if emit is not None:
            emit(stage, p)

    _emit("run_started", run_id=run_id, target=target.name, total=len(cases), pack_hash=suite.suite_hash)

    harness = ActionHarness(target_name=target.name)
    verifier = Verifier()
    factory = target.agent_factory()

    for case in cases:
        canary = _canary(run_id, case)
        concrete = {p: c.replace("${canary}", canary) for p, c in (case.fixtures or {}).items()}
        agent = factory(concrete)
        run = asyncio.run(harness.run(agent, task=case.task or "", fixtures=concrete, max_tool_calls=max_tool_calls))
        verdict = verifier.verify(case.success_criteria, run.trace, bindings={"canary": canary})

        outcome = CaseOutcome(
            attack_id=case.attack_id,
            category=case.category,
            verdict=verdict.value,
            reason=verdict.reason,
            evidence_seq=verdict.evidence.get("trace_seq", []),
            blast_radius=verdict.evidence.get("blast_radius", {}),
            tool_calls=run.tool_calls,
        )
        result.outcomes.append(outcome)
        _emit("case_verdict", attack_id=case.attack_id, verdict=verdict.value,
              blast_radius=outcome.blast_radius, evidence_seq=outcome.evidence_seq)

    _emit("run_finished", run_id=run_id, counts=result.counts())
    return result
