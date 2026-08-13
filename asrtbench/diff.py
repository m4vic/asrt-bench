"""Compare two saved versions: what got newly broken, newly fixed, or stayed.

This is the regression answer -- "did my update make it worse." It is a pure
function over two saved runs; nothing executes here.

Two rules keep a diff from lying, both carried from what SafetyDiff needed and
never fully got:

1. **Pack identity.** Two versions are comparable only if they were fired with
   the same pack (same content hash). A shift caused by a different pack is not
   a regression. Mismatched packs are refused, not warned about.
2. **Zero overlap is an error.** If the two runs share no attacks, there is
   nothing to compare -- that must raise, never render as "no change".

`unclear` is never counted as pass or fail. A transition into or out of unclear
is reported as an inconclusive change, kept apart from real broken/fixed moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from asrtbench.runner import RunResult


class IncomparableRuns(ValueError):
    """The two runs cannot be honestly compared."""


@dataclass
class AttackChange:
    attack_id: str
    baseline: str      # verdict in the baseline run
    candidate: str     # verdict in the candidate run


@dataclass
class DiffReport:
    baseline: str
    candidate: str
    pack_hash: str
    newly_broken: list[AttackChange] = field(default_factory=list)   # became exploitable
    newly_fixed: list[AttackChange] = field(default_factory=list)    # stopped being exploitable
    stable_broken: list[str] = field(default_factory=list)           # exploitable in both
    stable_safe: list[str] = field(default_factory=list)             # safe in both
    inconclusive: list[AttackChange] = field(default_factory=list)   # a move involving unclear
    only_in_baseline: list[str] = field(default_factory=list)
    only_in_candidate: list[str] = field(default_factory=list)

    def verdict(self) -> str:
        """A one-word headline. Broken beats fixed -- a regression is the thing
        you must not miss, so it dominates the summary."""
        if self.newly_broken:
            return "regressed"
        if self.newly_fixed:
            return "improved"
        return "unchanged"


def _is_success(v: str) -> bool:
    return v == "success"


def compare(baseline: RunResult, candidate: RunResult, *,
            baseline_name: str = "baseline", candidate_name: str = "candidate") -> DiffReport:
    # Gate 1: pack identity.
    if baseline.pack_hash != candidate.pack_hash:
        raise IncomparableRuns(
            f"different attack packs: {baseline_name} used {baseline.pack_hash[:12]}, "
            f"{candidate_name} used {candidate.pack_hash[:12]}. A diff across different "
            "packs is not a regression -- re-run both versions with the same pack."
        )

    base = {o.attack_id: o.verdict for o in baseline.outcomes}
    cand = {o.attack_id: o.verdict for o in candidate.outcomes}
    common = set(base) & set(cand)

    # Gate 2: zero overlap.
    if not common:
        raise IncomparableRuns(
            "the two runs share no attacks -- nothing to compare. This is an error, "
            "not a 'no change' result."
        )

    report = DiffReport(baseline=baseline_name, candidate=candidate_name, pack_hash=baseline.pack_hash)

    for attack_id in sorted(common):
        b, c = base[attack_id], cand[attack_id]
        if b == "unclear" or c == "unclear":
            if b != c:
                report.inconclusive.append(AttackChange(attack_id, b, c))
            else:
                # unclear in both -- no change, and nothing to claim either way
                pass
            continue
        if _is_success(c) and not _is_success(b):
            report.newly_broken.append(AttackChange(attack_id, b, c))
        elif _is_success(b) and not _is_success(c):
            report.newly_fixed.append(AttackChange(attack_id, b, c))
        elif _is_success(b) and _is_success(c):
            report.stable_broken.append(attack_id)
        else:
            report.stable_safe.append(attack_id)

    report.only_in_baseline = sorted(set(base) - common)
    report.only_in_candidate = sorted(set(cand) - common)
    return report
