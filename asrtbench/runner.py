"""Fire a pack at a target and collect per-attack verdicts.

This is the engine behind `/run`. It loads a pack, mints a per-case canary, runs
each attack against the selected target, and verifies deterministically.
`unclear` is kept as its own outcome -- never folded into a pass rate, never
counted as pass or fail.

One loop drives every target shape, because each one answers the same question:
given a task and a poisoned payload, what did your tools actually do?

- `Target` -- asrt-bench's own inert tools. Nothing is emailed, written, queried
  or executed for real. Safe by construction, no setup.
- `PythonTarget` -- your own `.py` file. Your flow runs untouched while the tool
  functions you named are recorded.
- `AttachedTarget` -- your agent wired through `asrtbench.attach`, where
  asrt-bench drives the model's tool-calling loop.

The last two use YOUR real tools, so side effects are real in whatever system
you pointed at. All three produce the same `RunResult`, so `/diff` compares them
identically.

Diagram box: THE LOOP + RESULTS — drives every case, collects the outcomes.
Full box -> file map: docs/modules_keywords.md
"""

from __future__ import annotations

import glob
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from asrtbench.adjudication import Verifier
from asrtbench.core import AttackCase, Suite, Trace


class PackTarget(Protocol):
    """What `run_pack` needs from a target: a name, and a way to run one attack.

    Keeping this surface to two members is what lets one loop drive a config
    file, your own Python agent, and an attached tool-loop without branching on
    the target's type. Anything that returns a Trace can join by implementing it.
    """

    name: str

    def run_case(self, task: str, fixtures: dict[str, str]) -> Trace: ...


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
    """A marker unique to this run AND this attack.

    Substituted into the poisoned text, and referenced by the attack's criteria.
    A tool argument carrying it proves the data came from *this* poisoned input
    rather than from a coincidence or a model inventing a plausible-looking value.
    """
    return f"ASRT-CANARY-{run_id}-{case.attack_id.replace(':', '-')}"


def run_pack(
    target: PackTarget,
    pack_dir: str | None = None,
    *,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> RunResult:
    """Run every attack in a pack against one target.

    `emit` is an optional progress callback, called with a stage name and a dict.
    It exists so the CLI can print a verdict per case as it happens without this
    module knowing anything about rendering.
    """
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

    _emit("run_started", run_id=run_id, target=target.name, total=len(cases),
          pack_hash=suite.suite_hash)

    verifier = Verifier()

    for case in cases:
        canary = _canary(run_id, case)
        fixtures = {
            path: content.replace("${canary}", canary)
            for path, content in (case.fixtures or {}).items()
        }

        trace = target.run_case(case.task or "", fixtures)
        verdict = verifier.verify(case.success_criteria, trace, bindings={"canary": canary})

        outcome = CaseOutcome(
            attack_id=case.attack_id,
            category=case.category,
            verdict=verdict.value,
            reason=verdict.reason,
            evidence_seq=verdict.evidence.get("trace_seq", []),
            blast_radius=verdict.evidence.get("blast_radius", {}),
            # Counted from the trace rather than reported by the target, so the
            # number always matches the evidence a reader can actually see.
            tool_calls=sum(1 for event in trace.events if event.kind == "tool_call"),
        )
        result.outcomes.append(outcome)
        _emit("case_verdict", attack_id=case.attack_id, verdict=verdict.value,
              blast_radius=outcome.blast_radius, evidence_seq=outcome.evidence_seq)

    _emit("run_finished", run_id=run_id, counts=result.counts())
    return result


def run_pack_on_attached(
    attached: PackTarget,
    pack_dir: str | None = None,
    *,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> RunResult:
    """Deprecated alias for `run_pack`, kept so existing scripts keep working.

    Attached targets used to need a separate loop; they now implement the same
    `run_case` as every other target, so there is one code path. Prefer
    `run_pack` -- this wrapper will be removed in a later version.
    """
    return run_pack(attached, pack_dir, emit=emit)
