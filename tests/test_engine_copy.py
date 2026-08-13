"""Smoke test for the copied engine (Stage 2, increment 1).

Proves two things the whole product rests on:

1. The engine ASRT hardened -- Harness + Verifier + the 7-capability corpus --
   actually runs inside asrt-bench's own package, off its own imports.
2. The structural boundary holds: this repo contains no attack-generation code.
   That is asserted here, in a test, so it can never silently drift back in.
"""

from __future__ import annotations

import asyncio
import glob
import importlib
import os
import pkgutil

import asrtbench
from asrtbench.adjudication import Verifier
from asrtbench.core import AttackCase
from asrtbench.harness import ActionHarness, DirectiveFollowingAgent


PACK_DIR = os.path.join(os.path.dirname(asrtbench.__file__), "packs", "starter")
CANARY = "ASRT-CANARY-smoke-0001"


def _load_pack() -> list[AttackCase]:
    cases: list[AttackCase] = []
    for path in sorted(glob.glob(os.path.join(PACK_DIR, "*.json"))):
        cases.extend(c for c in AttackCase.load_file(path) if c.channel == "action")
    return cases


def _run(case: AttackCase, canary: str = CANARY):
    concrete = {p: c.replace("${canary}", canary) for p, c in case.fixtures.items()}
    agent = DirectiveFollowingAgent(sorted(concrete))
    harness = ActionHarness(target_name="smoke-fixture")
    result = asyncio.run(harness.run(agent, task=case.task, fixtures=concrete, max_tool_calls=6))
    return Verifier().verify(case.success_criteria, result.trace, bindings={"canary": canary})


# ---------- the engine runs here ----------

def test_starter_pack_loads():
    cases = _load_pack()
    assert len(cases) >= 12  # the 7-capability corpus copied from the hardened ASRT
    for case in cases:
        assert case.channel == "action"
        assert case.success_criteria


def test_an_attack_lands_and_a_control_defends():
    by_id = {c.attack_id: c for c in _load_pack()}
    assert _run(by_id["action_channel:python_exec:001"]).value == "success"
    assert _run(by_id["action_channel:python_exec:control"]).value == "failure"


def test_blast_radius_spans_the_full_range():
    by_id = {c.attack_id: c for c in _load_pack()}
    verdict = _run(by_id["action_channel:python_exec:001"])
    assert verdict.evidence["blast_radius"]["weight"] == 10  # code_exec, the top of the range


# ---------- the boundary holds (ADR-0012) ----------

def test_no_generation_code_in_the_package():
    """asrt-bench must not contain the forge/mutator/optimizer/playbook/judge.

    This is the security property of the whole repo, asserted as code: a public
    tool that could generate attacks is a different, more dangerous thing than
    one that only replays them.
    """
    forbidden = {"discovery", "mutator", "optimizers", "grammar", "playbook",
                 "forge", "flywheel", "judge"}
    found = set()
    for mod in pkgutil.walk_packages(asrtbench.__path__, prefix="asrtbench."):
        leaf = mod.name.rsplit(".", 1)[-1]
        if leaf in forbidden:
            found.add(mod.name)
    assert not found, f"generation/judge modules leaked into asrt-bench: {found}"


def test_every_module_imports_cleanly():
    """No copied module secretly reaches back into a private ASRT package."""
    for mod in pkgutil.walk_packages(asrtbench.__path__, prefix="asrtbench."):
        importlib.import_module(mod.name)  # raises if an import edge is broken
