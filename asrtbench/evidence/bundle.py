"""Run bundle export -- the ASRT -> SafetyDiff contract (T15).

A run bundle is one self-describing artifact carrying everything needed to
compare two runs: the suite hash, the pinned run config, per-case verdicts with
evidence IDs, trace completeness, blast radius, reproduction rate, and the
adjudicator's identity. Everything in SafetyDiff (Phase G) consumes this and
nothing else.

`unclear` is a first-class count here and is never folded into a pass rate. The
adjudicator for the action channel is the deterministic Verifier, so there is no
judge kappa to report -- the bundle says so explicitly rather than leaving a
blank that could be mistaken for an unvalidated judge.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

BUNDLE_VERSION = "asrt.run-bundle/v1"


def build_run_bundle(report: Any) -> dict[str, Any]:
    """Serialize a CampaignReport into the portable bundle contract."""
    cases = []
    for o in report.outcomes:
        cases.append({
            "attack_id": o.attack_id,
            "category": o.category,
            "verdict": o.verdict_value,
            "reproduction": {"runs": o.runs, "successes": o.successes,
                             "failures": o.failures, "unclears": o.unclears,
                             "rate": o.reproduction_rate},
            "evidence_seq": o.evidence_seq,
            "blast_radius": o.blast_radius,
            "complete": o.complete,
            "error": o.error,
        })

    counts = report.counts()
    return {
        "bundle_version": BUNDLE_VERSION,
        "run_id": report.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": report.target,
        # The action channel is adjudicated deterministically. State it, so a
        # reader never mistakes the absence of a judge kappa for an unvalidated one.
        "adjudicator": {"kind": "verifier", "deterministic": True, "judge_kappa": None},
        "run_config": report.run_config.to_dict() if report.run_config else None,
        "suite": report.suite.to_dict() if report.suite else None,
        "counts": counts,
        "cases": cases,
    }


def write_run_bundle(report: Any, path: str) -> str:
    bundle = build_run_bundle(report)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(bundle, handle, indent=2)
    return path
