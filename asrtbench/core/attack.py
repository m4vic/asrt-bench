"""Attack schema and loader for ASRT.

This module converts mixed attack JSON records into one `AttackCase` shape.
ASRT uses this normalized shape for selection, generation, improvement, and
later SafetyDiff comparisons.
"""

from dataclasses import dataclass, field
from typing import Any
import hashlib
import json
from pathlib import Path



@dataclass
class AttackCase:
    attack_id: str
    prompt: str
    collection: str
    category: str
    origin: str # predefined or auto attack gen

    # The text channel is the compatibility default for the existing corpus.
    # Action-channel cases are evaluated by the deterministic Verifier against
    # an instrumented Harness Trace, never by the text judge.
    channel: str = "text"
    success_criteria: dict[str, Any] | None = None

    # Action-channel only. `task` is what the agent is asked to do; `fixtures`
    # is the in-memory file content it can read (one of which carries the
    # untrusted injection). Both stay None for text attacks.
    task: str | None = None
    fixtures: dict[str, str] | None = None

    intent: str | None = None
    severity_label: str | None = None
    severity_hint: float | None = None
    expected_violation: bool | None = None  
    tags: list[str] = field(default_factory=list)

    source_name: str | None = None
    source_file: str | None = None 
    original_id: str | None = None
    parent_ids: list[str] = field(default_factory=list)
    sequence_id: str | None = None 
    description: str | None = None 
    status: str = "validated"
    metadata: dict = field(default_factory=dict)


    @staticmethod 
    def infer_intent(category: str, prompt: str, tags: list[str]) -> str | None:
        text = f"{category} {' '.join(tags)} {prompt}".lower()

        if category in {"benign", "control"}:
            return "benign"
        if "system prompt" in text or "reveal your instructions" in text:
            return "system_extraction"
        if "you are now" in text or "persona" in text or "dan" in text:
            return "role_hijack"
        if "delete_all_files" in text or "execute_command" in text or "admin" in text:
            return "tool_abuse"
        if "api key" in text or "training data" in text or "confidential" in text:
            return "data_exfiltration"
        if "ignore previous instructions" in text or "override" in text:
            return "instruction_override"
        if "bypass" in text or "disable safety" in text or "unrestricted" in text:
            return "policy_bypass"

        return None
    

    @staticmethod # not need self but belongs to class 

    def severity_from_hint(severity_hint: float | None) -> str | None:
        if severity_hint is None:
            return None
        if severity_hint >= 0.85:
            return "critical"
        if severity_hint >= 0.65:
            return "high"
        if severity_hint >= 0.35:
            return "medium"
        if severity_hint > 0:
            return "low"
        return "none"
    

    @classmethod   
    def from_raw(cls, item: dict, source_file: str, origin: str= "pd") -> "AttackCase":
        prompt = item.get("prompt") or item.get("text")

        # Action-channel attacks carry their payload in `fixtures`, not a text
        # prompt; the task stands in for the prompt slot so the shared schema
        # still has a non-empty descriptor to key intent and digests on.
        if not prompt and item.get("channel") == "action":
            prompt = item.get("task")

        if not prompt:
            raise ValueError(f"Missing prompt/text in {source_file}")
        

        path = Path(source_file)
        collection = path.parent.name
        category = item.get("category") or collection
        tags = list(item.get("tags", []))

        severity_label = item.get("severity")
        severity_hint = item.get("severity_hint")

        if severity_label is None:
            severity_label = cls.severity_from_hint(severity_hint)

        attack_id = item.get("id")
        if attack_id is None:
            # A content digest, not Python's per-process salted hash(): the same
            # attack file must yield the same id in every interpreter, or no run
            # can be joined to another and SafetyDiff has nothing to compare.
            digest = hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()[:12]
            attack_id = f"{collection}:{path.stem}:{digest}"
        
        description = item.get("description") or item.get("note") or item.get("notes") 
        metadata = {}
        known_keys = {
            "id",
            "original_id",
            "prompt",
            "text",
            "category",
            "tags",
            "severity",
            "severity_hint",
            "expected_violation",
            "channel",
            "success_criteria",
            "task",
            "fixtures",
            "sequence_id",
            "description",
            "note",
            "notes",
        }


        for key, value in item.items():
            if key not in known_keys:
                metadata[key] = value

        return cls(
            attack_id=attack_id,
            prompt=prompt.strip(),
            collection=collection,
            category=category,
            intent=cls.infer_intent(category, prompt, tags),
            tags=tags,
            severity_label=severity_label,
            severity_hint=severity_hint,
            expected_violation=item.get("expected_violation"),
            origin=origin,
            channel=item.get("channel", "text"),
            success_criteria=item.get("success_criteria"),
            task=item.get("task"),
            fixtures=item.get("fixtures"),
            source_name=path.stem,
            source_file=str(path),
            original_id=item.get("original_id"),
            sequence_id=item.get("sequence_id"),
            description=description,
            metadata=metadata,
        )
    

    @classmethod 
    def load_file(cls, source_file: str, origin:str = "pd") -> list["AttackCase"]:
        with open(source_file, "r", encoding="utf-8") as handle:
            raw_items = json.load(handle)

        attacks = []
        for item in raw_items:
            attacks.append(cls.from_raw(item, source_file, origin))

        return attacks 
    
if __name__ == "__main__":
    attacks = AttackCase.load_file("data/attack_db/agent_manipulation/tool_exploitation.json")
    for attack in attacks[:3]:
        print(attack)




    









