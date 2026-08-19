"""The version store: saved runs a user can diff later.

A *version* is one saved run of a pack against a target, under a name the user
chooses (`v1`, `before-guardrail`, ...). Stored as plain JSON on the user's
machine -- nothing leaves it. `/diff` reads two of these back and compares them.

The store keeps the pack hash with every version, because that is the only thing
that makes two versions honestly comparable: a diff between runs fired with
different packs is not a regression, it is a different question.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from asrtbench.runner import CaseOutcome, RunResult


def store_dir() -> Path:
    """Where versions live. Under the current working directory so a user's
    saved runs sit with their project, not hidden in the package."""
    d = Path(os.environ.get("ASRT_BENCH_STORE", "runs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(result: RunResult, name: str) -> Path:
    """Save a run under a version name. Overwrites an existing version of the
    same name, after the caller has been given a chance to see it (the CLI
    prints a note); history beyond the latest is out of scope for v0.1."""
    path = store_dir() / f"{name}.json"
    payload = {
        "version": name,
        "saved_at": time.time(),
        "run_id": result.run_id,
        "target": result.target,
        "pack_hash": result.pack_hash,
        "counts": result.counts(),
        "outcomes": [asdict(o) for o in result.outcomes],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def exists(name: str) -> bool:
    return (store_dir() / f"{name}.json").is_file()


def load(name: str) -> RunResult:
    path = store_dir() / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no saved version '{name}' (looked in {store_dir()})")
    data = json.loads(path.read_text())
    outcomes = [CaseOutcome(**o) for o in data["outcomes"]]
    return RunResult(
        run_id=data["run_id"],
        target=data["target"],
        pack_hash=data["pack_hash"],
        outcomes=outcomes,
    )


def meta(name: str) -> dict:
    """The saved metadata for a version, without rebuilding the RunResult."""
    data = json.loads((store_dir() / f"{name}.json").read_text())
    return {k: data[k] for k in ("run_id", "version", "saved_at", "target", "pack_hash", "counts")}


def list_versions() -> list[dict]:
    """Every saved version, newest first."""
    out = []
    for path in store_dir().glob("*.json"):
        try:
            out.append(meta(path.stem))
        except Exception:
            continue
    return sorted(out, key=lambda m: m["saved_at"], reverse=True)
