"""Persistence for action-channel runs (T4).

A deliberately separate table from `results`. A deterministic Verifier verdict
(with evidence sequence IDs and a criteria version) and a text-judge score are
different kinds of claim with different trust; merging them into one row invites
averaging them into a single meaningless number.

This layer persists verbatim and rehydrates verbatim. It never re-decides a
verdict or edits a trace -- evidence may not alter what it is handed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from asrtbench.core import Trace, Verdict

from .database import DBManager


def ensure_action_table(db: DBManager) -> None:
    autoinc = "SERIAL PRIMARY KEY" if db.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS action_runs (
            id {autoinc},
            run_id VARCHAR(100),
            attack_id VARCHAR(255),
            target VARCHAR(255),
            criteria_version VARCHAR(100),
            verdict_value VARCHAR(50),
            verdict_source VARCHAR(50),
            evidence_seq TEXT,
            complete INTEGER,
            criteria TEXT,
            bindings TEXT,
            verdict TEXT,
            trace TEXT,
            created_at VARCHAR(100)
        );
        """
    )


def save_action_run(
    db: DBManager,
    *,
    run_id: str,
    attack_id: str,
    target: str,
    criteria: dict[str, Any],
    bindings: dict[str, Any],
    verdict: Verdict,
    trace: Trace,
) -> None:
    """Store one action run exactly as produced, with enough to re-verify it."""
    ensure_action_table(db)
    complete, _, _ = trace.completeness()
    db.execute(
        """
        INSERT INTO action_runs (
            run_id, attack_id, target, criteria_version, verdict_value,
            verdict_source, evidence_seq, complete, criteria, bindings,
            verdict, trace, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            run_id,
            attack_id,
            target,
            verdict.criteria_version,
            verdict.value,
            verdict.source,
            json.dumps(verdict.evidence.get("trace_seq", [])),
            1 if complete else 0,
            json.dumps(criteria),
            json.dumps(bindings),
            json.dumps(verdict.to_dict()),
            json.dumps(trace.to_dict()),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def load_action_runs(db: DBManager, attack_id: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
    """Return stored action runs, each rehydrated enough to re-verify.

    Each row carries the parsed ``criteria`` and ``bindings``, the stored
    ``verdict`` dict, and a rebuilt ``Trace`` object under ``trace``.
    """
    ensure_action_table(db)
    query = (
        "SELECT run_id, attack_id, target, criteria_version, verdict_value, "
        "verdict_source, evidence_seq, complete, criteria, bindings, verdict, "
        "trace, created_at FROM action_runs"
    )
    conditions = []
    params: list[Any] = []
    if attack_id is not None:
        conditions.append("attack_id = %s")
        params.append(attack_id)
    if run_id is not None:
        conditions.append("run_id = %s")
        params.append(run_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id ASC"

    rows = db.fetchall(query, tuple(params))
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "run_id": row[0],
                "attack_id": row[1],
                "target": row[2],
                "criteria_version": row[3],
                "verdict_value": row[4],
                "verdict_source": row[5],
                "evidence_seq": json.loads(row[6]) if row[6] else [],
                "complete": bool(row[7]),
                "criteria": json.loads(row[8]) if row[8] else {},
                "bindings": json.loads(row[9]) if row[9] else {},
                "verdict": json.loads(row[10]) if row[10] else {},
                "trace": Trace.from_dict(json.loads(row[11])) if row[11] else None,
                "created_at": row[12],
            }
        )
    return out
