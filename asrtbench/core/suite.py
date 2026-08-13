"""Frozen, hashed, immutable attack suites (T13).

A suite is a named selection of attacks with a content hash. "Frozen" is not a
property of the files on disk; it is enforced here: the hash covers each
attack's identity AND its content (prompt digest, criteria digest, channel), so
a changed criterion produces a different suite -- it can never silently pass as
the same measurement. Adding or changing an attack makes a new suite version; it
never mutates an existing one.

The suite hash goes in every run's RunConfig, which is how two runs are proven
to have measured the same thing (or proven not to).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .attack import AttackCase


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _fingerprint(attack: AttackCase) -> dict[str, Any]:
    """The content that must not change without the suite hash changing."""
    criteria = attack.success_criteria or {}
    return {
        "attack_id": attack.attack_id,
        "channel": attack.channel,
        "prompt": _digest(attack.prompt or ""),
        "criteria": _digest(json.dumps(criteria, sort_keys=True)),
    }


@dataclass(frozen=True)
class Suite:
    name: str
    version: str
    attack_ids: tuple[str, ...]
    fingerprints: tuple[tuple, ...]  # frozen (key, value) pairs per attack, sorted
    suite_hash: str

    @classmethod
    def freeze(cls, name: str, attacks: list[AttackCase], *, version: str = "v1") -> "Suite":
        prints = sorted((_fingerprint(a) for a in attacks), key=lambda p: p["attack_id"])
        payload = json.dumps({"name": name, "version": version, "attacks": prints}, sort_keys=True)
        suite_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return cls(
            name=name,
            version=version,
            attack_ids=tuple(p["attack_id"] for p in prints),
            fingerprints=tuple(tuple(sorted(p.items())) for p in prints),
            suite_hash=suite_hash,
        )

    def __len__(self) -> int:
        return len(self.attack_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "suite_hash": self.suite_hash,
            "attack_ids": list(self.attack_ids),
        }
