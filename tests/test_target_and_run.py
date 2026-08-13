"""Target loading + the fire-a-pack-at-a-target loop (Stage 2, increment 2).

Proves the concrete "example target + pack -> test it" flow the tool is for,
across all three target shapes: the deterministic fixture, a local model, and a
hosted OpenAI-compatible endpoint. The two model paths are exercised without a
real server -- what matters is that the right chat_fn is built and the request
it forms is well-shaped.
"""

from __future__ import annotations

import json

import pytest

from asrtbench.harness.model_agent import _to_openai_messages, TOOL_SCHEMAS
from asrtbench.runner import run_pack, load_pack, starter_pack_dir
from asrtbench.target import Target


# ---------- target configs load correctly ----------

def test_bundled_example_targets_all_load():
    names = Target.list_available()
    for expected in ("fixture", "local-ollama", "email-only", "hosted-example"):
        assert expected in names
        Target.resolve(expected)  # raises if malformed


def test_fixture_target_is_deterministic():
    t = Target.resolve("fixture")
    assert t.describe()["deterministic"] is True


def test_tool_subset_narrows_the_capability_surface():
    full = Target.resolve("local-ollama").describe()
    narrow = Target.resolve("email-only").describe()
    assert "network_egress" in full["capabilities"]
    assert "network_egress" not in narrow["capabilities"]
    assert narrow["tools"] == ["read_file", "send_email"]


def test_hosted_target_records_endpoint_and_provider():
    d = Target.resolve("hosted-example").describe()
    assert d["provider"] == "openai"
    assert d["endpoint"] == "https://api.openai.com/v1"


def test_unknown_provider_is_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"kind": "model", "provider": "anthropic-magic", "model": "x"}))
    with pytest.raises(ValueError):
        Target.from_file(p)


# ---------- the worked example: fixture target + starter pack ----------

def test_fire_starter_pack_at_fixture_target():
    """The end-to-end loop a user runs: a target + a pack -> verdicts."""
    target = Target.resolve("fixture")
    result = run_pack(target, starter_pack_dir())

    by_id = {o.attack_id: o for o in result.outcomes}
    # A high-severity attack lands; its benign control defends.
    assert by_id["action_channel:python_exec:001"].verdict == "success"
    assert by_id["action_channel:python_exec:control"].verdict == "failure"
    # The run carries the pack's content hash -- the diff gate depends on it.
    assert result.pack_hash
    counts = result.counts()
    assert counts["success"] >= 1 and counts["total"] == len(load_pack(starter_pack_dir()))


def test_same_pack_two_runs_share_a_pack_hash():
    """Two runs of the same pack must be diff-comparable; the hash proves it."""
    target = Target.resolve("fixture")
    a = run_pack(target, starter_pack_dir())
    b = run_pack(target, starter_pack_dir())
    assert a.pack_hash == b.pack_hash


# ---------- hosted endpoint: correct request shape, no live call ----------

def test_openai_message_translation_is_wire_valid():
    """The harness's loose messages must become strict OpenAI messages:
    assistant tool calls get ids + JSON arguments; tool results cite them."""
    internal = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "read the file"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "read_file", "args": {"path": "x.md"}}]},
        {"role": "tool", "name": "read_file", "content": "file contents"},
    ]
    wire = _to_openai_messages(internal)

    assistant = wire[2]
    assert assistant["tool_calls"][0]["type"] == "function"
    call_id = assistant["tool_calls"][0]["id"]
    # arguments must be a JSON string, not a dict
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "x.md"}
    # the tool result must cite the exact id of the call it answers
    assert wire[3]["role"] == "tool"
    assert wire[3]["tool_call_id"] == call_id


def test_openai_target_builds_a_callable_factory():
    """A hosted target must produce a working agent factory even offline --
    only an actual run would need the network."""
    target = Target.resolve("hosted-example")
    factory = target.agent_factory()
    agent = factory({})
    assert agent is not None
