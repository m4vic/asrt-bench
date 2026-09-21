"""Frozen, hashed, immutable attack suites (T13).

A suite is a named selection of attacks with a content hash. "Frozen" is not a
property of the files on disk; it is enforced here: the hash covers each
attack's identity AND everything that decides what it measures -- the task, the
poisoned fixtures, the success criteria, the channel. Change any of them and the
suite hash changes, so an edited attack can never silently pass as the same
measurement. Adding or changing an attack makes a new suite version; it never
mutates an existing one.

The suite NAME is deliberately not hashed. Identity is content, not location --
copying a pack to another folder, or renaming it, must not make two otherwise
identical runs incomparable.

The suite hash goes in every run's RunConfig, which is how two runs are proven
to have measured the same thing (or proven not to).

Diagram box: PACK FINGERPRINT — computes the pack_hash the diff gate checks.
Full box -> file map: docs/modules_keywords.md
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
    """The content that must not change without the suite hash changing.

    `fixtures` is included because for an action-channel attack it *is* the
    attack: the poisoned document the agent reads. `prompt` only holds the
    innocent-looking task ("Read briefing.md and summarize it"). Leaving fixtures
    out meant the injection payload could be rewritten entirely while the hash
    stayed identical, and `/diff` would then report a newly-landing attack as a
    regression in the agent rather than as a different attack -- exactly the
    confusion the pack-identity gate exists to prevent.
    """
    criteria = attack.success_criteria or {}
    fixtures = attack.fixtures or {}
    return {
        "attack_id": attack.attack_id,
        "channel": attack.channel,
        "prompt": _digest(attack.prompt or ""),
        "fixtures": _digest(json.dumps(fixtures, sort_keys=True)),
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
        # `name` is metadata, not identity. The runner derives it from the pack
        # directory's basename, which is an accident of where the files happen to
        # sit -- hashing it made a renamed or copied pack look like a different
        # measurement. `version` stays in: bumping it is a deliberate declaration
        # that this is a new suite.
        payload = json.dumps({"version": version, "attacks": prints}, sort_keys=True)
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
