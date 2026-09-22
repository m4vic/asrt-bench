"""Attack schema and loader.

Turns the JSON records in a pack file into one uniform `AttackCase` shape, so
nothing downstream — the runner, the harness, the Verifier — ever has to touch
raw JSON or cope with a field being written three different ways.

This file only parses. It runs nothing and decides nothing about a verdict.

Diagram box: ATTACK PACK — the parser that turns pack JSON into objects.
Full box -> file map: docs/modules_keywords.md
"""

from dataclasses import dataclass, field
from typing import Any
import hashlib
import json
from pathlib import Path


@dataclass
class AttackCase:
    """One attack, normalized.

    Only `action`-channel cases are executable here: `runner.load_pack` filters
    on `channel`, so a `text` record loads fine and is then ignored. The text
    channel needed an LLM judge, which this tool deliberately does not have.
    """

    attack_id: str
    prompt: str
    collection: str        # the pack folder this came from; used to build a missing id
    category: str

    # `text` stays the default so an old corpus file still parses. Action cases
    # are judged deterministically by the Verifier against a Harness Trace.
    channel: str = "text"
    success_criteria: dict[str, Any] | None = None

    # Action-channel only. `task` is what the agent is asked to do; `fixtures` is
    # the file content it can read, one of which carries the injection. For an
    # action attack the fixtures ARE the attack — the task looks innocent.
    task: str | None = None
    fixtures: dict[str, str] | None = None

    # Written by whoever authored the pack and read by a human looking at
    # results. Nothing in the pipeline branches on these.
    tags: list[str] = field(default_factory=list)
    expected_violation: bool | None = None
    description: str | None = None
    source_file: str | None = None

    @classmethod
    def from_raw(cls, item: dict, source_file: str) -> "AttackCase":
        """Build one case from one raw JSON record.

        Raises on a record with no usable descriptor rather than constructing a
        half-empty case, because a silently malformed attack would be fired and
        counted like any other.
        """
        prompt = item.get("prompt") or item.get("text")

        # An action attack carries its payload in `fixtures`, not in a prompt.
        # The task fills the prompt slot so every case has a non-empty descriptor
        # to hash and to show in a report.
        if not prompt and item.get("channel") == "action":
            prompt = item.get("task")
        if not prompt:
            raise ValueError(f"Missing prompt/text/task in {source_file}")

        path = Path(source_file)
        collection = path.parent.name

        attack_id = item.get("id")
        if attack_id is None:
            # A content digest, not Python's hash(): that is salted per process,
            # so the same file would produce a different id on every run and no
            # two runs could ever be joined or diffed.
            digest = hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()[:12]
            attack_id = f"{collection}:{path.stem}:{digest}"

        return cls(
            attack_id=attack_id,
            prompt=prompt.strip(),
            collection=collection,
            category=item.get("category") or collection,
            channel=item.get("channel", "text"),
            success_criteria=item.get("success_criteria"),
            task=item.get("task"),
            fixtures=item.get("fixtures"),
            tags=list(item.get("tags", [])),
            expected_violation=item.get("expected_violation"),
            description=item.get("description") or item.get("note") or item.get("notes"),
            source_file=str(path),
        )

    @classmethod
    def load_file(cls, source_file: str) -> list["AttackCase"]:
        """Read one pack file and return every case in it."""
        with open(source_file, "r", encoding="utf-8") as handle:
            raw_items = json.load(handle)
        return [cls.from_raw(item, source_file) for item in raw_items]
