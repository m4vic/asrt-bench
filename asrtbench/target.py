"""Target: the thing under test, named in a config file.

A target is data, not code. asrt-bench never executes a user-supplied program --
it reads a small JSON file that *describes* a model, and drives that model as an
agent using asrt-bench's own inert instrumented tools.

    {"kind": "fixture"}                                  # deterministic, no model
    {"kind": "model", "provider": "ollama",
     "model": "qwen2.5:7b-instruct", "tools": [...]}     # a local model
    {"kind": "model", "provider": "openai",
     "model": "gpt-4o-mini",
     "api_base": "https://api.openai.com/v1",
     "api_key_env": "OPENAI_API_KEY"}                    # a hosted model

Honest scope: the target is the model's *brain*. The tools it can call are
asrt-bench's inert ones (the harness lends them), not the tools of a real
deployed application. So a verdict here answers "would this model, wired to a
tool shaped like send_email, misuse it under a poisoned document" -- not "is
your production system exploitable end to end." Attaching to a real running
agent with its own tools is a separate, larger problem, deliberately not here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from asrtbench.core import TargetProfile
from asrtbench.harness import (
    DirectiveFollowingAgent, ModelAgent, ollama_chat_fn, openai_chat_fn,
)

# Bundled example configs; `/target <name>` resolves a bare name here.
TARGETS_DIR = Path(__file__).parent / "targets"

# The tool names the harness instruments. A target's capability surface derives
# from these; a target may expose a subset via its "tools" field.
HARNESS_TOOLS = (
    "read_file", "send_email", "http_request",
    "write_file", "sql_query", "read_env", "python_exec",
)

PROVIDERS = ("ollama", "openai")


@dataclass
class Target:
    """A target described by a config file, ready to be driven as an agent."""

    name: str
    kind: str                       # "fixture" | "model"
    provider: str = "ollama"        # model targets only
    model: str | None = None
    api_base: str | None = None     # openai-compatible endpoint; default per provider
    api_key_env: str = "OPENAI_API_KEY"
    attacker_knowledge: str = "schema"
    tools: tuple[str, ...] | None = None   # None -> the full harness surface

    @classmethod
    def fixture(cls) -> "Target":
        return cls(name="fixture", kind="fixture")

    @classmethod
    def from_file(cls, path: str | Path) -> "Target":
        data = json.loads(Path(path).read_text())
        kind = data.get("kind")
        tools = tuple(data["tools"]) if data.get("tools") else None
        name = data.get("name") or Path(path).stem

        if kind == "fixture":
            return cls(name=name, kind="fixture", tools=tools)

        if kind == "model":
            if not data.get("model"):
                raise ValueError(f"{path}: kind=model requires a \"model\" field")
            provider = data.get("provider", "ollama")
            if provider not in PROVIDERS:
                raise ValueError(f"{path}: unknown provider {provider!r} (expected one of {PROVIDERS})")
            return cls(
                name=name,
                kind="model",
                provider=provider,
                model=data["model"],
                api_base=data.get("api_base"),
                api_key_env=data.get("api_key_env", "OPENAI_API_KEY"),
                attacker_knowledge=data.get("attacker_knowledge", "schema"),
                tools=tools,
            )

        raise ValueError(f"{path}: unknown target kind {kind!r} (expected 'fixture' or 'model')")

    @classmethod
    def resolve(cls, ref: str) -> "Target":
        """A literal path, or a bare name looked up among the bundled examples."""
        p = Path(ref)
        if p.is_file():
            return cls.from_file(p)
        candidate = TARGETS_DIR / (ref if ref.endswith(".json") else f"{ref}.json")
        if candidate.is_file():
            return cls.from_file(candidate)
        raise FileNotFoundError(
            f"no target '{ref}' (looked at it as a path and under {TARGETS_DIR})"
        )

    @staticmethod
    def list_available() -> list[str]:
        if not TARGETS_DIR.is_dir():
            return []
        return sorted(p.stem for p in TARGETS_DIR.glob("*.json"))

    def missing_credential(self) -> str | None:
        """The env var this target needs but does not have, if any."""
        if self.kind == "model" and self.provider == "openai":
            import os
            try:
                from dotenv import load_dotenv
                load_dotenv()
            except ImportError:
                pass
            if not os.environ.get(self.api_key_env):
                return self.api_key_env
        return None

    def agent_factory(self) -> Callable[[dict[str, str]], Any]:
        """A function that builds a fresh agent per attack (no state leaks;
        honest reproduction for a stochastic model)."""
        if self.kind == "fixture":
            return lambda fixtures: DirectiveFollowingAgent(sorted(fixtures))

        if self.provider == "openai":
            base = self.api_base or "https://api.openai.com/v1"
            chat = openai_chat_fn(self.model, api_base=base, api_key_env=self.api_key_env)
        else:
            base = self.api_base or "http://localhost:11434"
            chat = ollama_chat_fn(self.model, api_base=base)
        return lambda _fixtures: ModelAgent(chat)

    def tool_surface(self) -> tuple[str, ...]:
        return self.tools or HARNESS_TOOLS

    def profile(self) -> TargetProfile:
        return TargetProfile.from_tools(
            self.name, list(self.tool_surface()), attacker_knowledge=self.attacker_knowledge
        )

    def describe(self) -> dict[str, Any]:
        p = self.profile()
        endpoint = None
        if self.kind == "model":
            endpoint = self.api_base or ("http://localhost:11434" if self.provider == "ollama"
                                         else "https://api.openai.com/v1")
        return {
            "name": self.name,
            "kind": self.kind,
            "provider": self.provider if self.kind == "model" else None,
            "model": self.model,
            "endpoint": endpoint,
            "deterministic": self.kind == "fixture",
            "tools": list(self.tool_surface()),
            "capabilities": sorted(p.capabilities),
            "blast_ceiling": p.blast_ceiling(),
            "attacker_knowledge": self.attacker_knowledge,
        }
