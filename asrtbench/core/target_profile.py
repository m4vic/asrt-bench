"""Target profile and threat model.

A real agentic system is not "an agent" -- it is a set of capabilities (by
class, not tool name), some guardrails, and an architecture (RAG, MCP, vector
DB). Describing a target this way lets a finding generalize across systems that
share a shape, and lets the same frozen attack run against different profiles to
show which capability + guardrail combination is actually exploitable.

The attacker-knowledge knob is part of the threat model: how much the attacker
knows about the target is a choice, not a fact. The gap between a BLIND attacker
and a SCHEMA-aware one measures how much a system's safety rests on obscurity --
fragile armor -- versus real guardrails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .capability import CAPABILITY_WEIGHT, TOOL_TO_CAPABILITY, weight_of_capability


class AttackerKnowledge(str, Enum):
    """What the attacker is allowed to know about the target's tools."""

    BLIND = "blind"        # nothing: must probe or guess (tool names are convergent, so guessing often works)
    CATEGORY = "category"  # knows capability classes ("it can send messages"), not names/signatures
    SCHEMA = "schema"      # knows exact tool names and signatures -- worst case / upper bound


@dataclass(frozen=True)
class TargetProfile:
    name: str
    capabilities: frozenset[str] = field(default_factory=frozenset)   # capability class names
    tools: tuple[str, ...] = ()                                        # concrete tool names it exposes
    guardrails: tuple[str, ...] = ()                                   # e.g. egress_filter, output_classifier
    architecture: tuple[str, ...] = ()                                # e.g. rag, mcp, vector_db
    attacker_knowledge: AttackerKnowledge = AttackerKnowledge.SCHEMA

    @classmethod
    def from_tools(
        cls,
        name: str,
        tools: list[str],
        *,
        guardrails: tuple[str, ...] = (),
        architecture: tuple[str, ...] = (),
        attacker_knowledge: AttackerKnowledge | str = AttackerKnowledge.SCHEMA,
    ) -> "TargetProfile":
        """Build a profile from concrete tool names, deriving capability classes."""
        caps = frozenset(TOOL_TO_CAPABILITY.get(t, "unknown") for t in tools)
        knowledge = AttackerKnowledge(attacker_knowledge) if isinstance(attacker_knowledge, str) else attacker_knowledge
        return cls(
            name=name,
            capabilities=caps,
            tools=tuple(tools),
            guardrails=tuple(guardrails),
            architecture=tuple(architecture),
            attacker_knowledge=knowledge,
        )

    def blast_ceiling(self) -> int:
        """The highest reach this target could suffer -- the worst possible blast radius."""
        if not self.capabilities:
            return 0
        return max(weight_of_capability(c) for c in self.capabilities)

    def visible_to_attacker(self) -> dict[str, Any]:
        """What the attacker is told, per the threat model.

        BLIND sees nothing. CATEGORY sees capability classes but not tool names.
        SCHEMA sees the concrete tool names. This is what an attacker executor
        (T19) is handed; it must not read more than this.
        """
        if self.attacker_knowledge == AttackerKnowledge.BLIND:
            return {"knowledge": "blind"}
        if self.attacker_knowledge == AttackerKnowledge.CATEGORY:
            return {"knowledge": "category", "capabilities": sorted(self.capabilities)}
        return {"knowledge": "schema", "tools": list(self.tools), "capabilities": sorted(self.capabilities)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": sorted(self.capabilities),
            "tools": list(self.tools),
            "guardrails": list(self.guardrails),
            "architecture": list(self.architecture),
            "attacker_knowledge": self.attacker_knowledge.value,
            "blast_ceiling": self.blast_ceiling(),
        }
