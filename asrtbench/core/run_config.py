"""Pinned run configuration and comparability (T8).

A run's identity is everything that could change its result: the target, the
generation settings, the system prompt, the tool schema, and the suite. Two runs
that differ in any of these are not measuring the same thing, and SafetyDiff
must be able to refuse to compare them (S4).

`seed` is recorded but deliberately does NOT break comparability: reproduction
runs (T14) vary the seed on purpose. Everything else must match.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# Fields that must be equal for two runs to be comparable. `seed` is excluded on
# purpose; `config_hash` is derived, not compared directly.
IDENTITY_FIELDS = (
    "target", "channel", "temperature", "top_p",
    "system_prompt_hash", "tool_schema_hash", "suite_hash",
)


@dataclass(frozen=True)
class RunConfig:
    target: str
    channel: str = "action"
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    system_prompt_hash: str = ""
    tool_schema_hash: str = ""
    suite_hash: str | None = None

    @classmethod
    def build(
        cls,
        *,
        target: str,
        channel: str = "action",
        system_prompt: str = "",
        tool_schemas: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        suite_hash: str | None = None,
    ) -> "RunConfig":
        return cls(
            target=target,
            channel=channel,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            system_prompt_hash=_sha(system_prompt or ""),
            tool_schema_hash=_sha(json.dumps(tool_schemas or [], sort_keys=True)),
            suite_hash=suite_hash,
        )

    def config_hash(self) -> str:
        return _sha(json.dumps(asdict(self), sort_keys=True, default=str))

    def is_comparable_to(self, other: "RunConfig") -> tuple[bool, list[str]]:
        """Return (comparable, differing_identity_fields).

        A difference in any identity field means the two runs cannot be diffed as
        the same measurement. `seed` differences are allowed and never reported.
        """
        diffs = [f for f in IDENTITY_FIELDS if getattr(self, f) != getattr(other, f)]
        return (not diffs, diffs)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["config_hash"] = self.config_hash()
        return d

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunConfig":
        fields = {k: payload.get(k) for k in (
            "target", "channel", "temperature", "top_p", "seed",
            "system_prompt_hash", "tool_schema_hash", "suite_hash",
        )}
        fields["target"] = fields["target"] or "unknown"
        fields["channel"] = fields["channel"] or "action"
        return cls(**fields)
