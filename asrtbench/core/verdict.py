"""Common, evidence-citing adjudication output.

Diagram box: VERIFIER — the answer it returns.
Full box -> file map: docs/modules_keywords.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Verdict:
    """Result emitted by either the deterministic Verifier or text Judge."""

    value: str
    source: str
    criteria_version: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "criteria_version": self.criteria_version,
            "evidence": self.evidence,
            "reason": self.reason,
        }
