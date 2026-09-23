# asrt-bench

**Fire a frozen attack pack at your AI agent, verify what lands, and diff safety across versions.**

[![License: MIT](https://img.shields.io/github/license/m4vic/asrt-bench?color=22d3ee)](https://github.com/m4vic/asrt-bench/blob/master/LICENSE)
[![Stars](https://img.shields.io/github/stars/m4vic/asrt-bench?style=flat&color=22d3ee)](https://github.com/m4vic/asrt-bench/stargazers)
[![Python](https://img.shields.io/badge/python-3.10%2B-22d3ee.svg)](https://github.com/m4vic/asrt-bench/blob/master/pyproject.toml)

<p align="center">
  <img src="https://raw.githubusercontent.com/m4vic/asrt-bench/master/docs/demo.gif" alt="asrt-bench demo — a poisoned support ticket drives a real fraudulent refund, and a hardening prompt stops nothing" width="820">
</p>

⚠️ **This project is constantly evolving and is currently not production-grade.**

asrt-bench answers one question, and answers it without a human or an LLM judge:

> Did a poisoned input drive your agent to misuse a tool — and did your last change make that better or worse?

Success is a **fact about a recorded tool call**, not an opinion about text. When the agent
calls `send_email` to an attacker domain with your secret in the body, that call is in the
trace or it isn't. No grader, no threshold, no drift.

## Install

```bash
pip install asrt-bench
asrt-bench
```

That installs the `asrt-bench` command. Prefer to run from a clone (the attack packs are plain
JSON you may want to read and extend)? That works too:

```bash
git clone https://github.com/m4vic/asrt-bench
cd asrt-bench
pip install -r requirements.txt
python -m asrtbench
```

Then, in the console:

```
asrt-bench ❯ /target fixture
asrt-bench ❯ /run name=v1
asrt-bench ❯ /run name=v2
asrt-bench ❯ /diff v1 v2
```

Requires Python 3.10+. Three dependencies (`rich`, `prompt_toolkit`, `python-dotenv`).

## Test your own agent

The point of asrt-bench is not the bundled fixture — it is pointing it at **your** agent and
watching the same attack set get *less* effective as you harden it, version by version.

Your agent decides which tool to call in your own code (a LangGraph node, a router, any
dispatcher). asrt-bench cannot see inside a program it did not build, so you give it a tiny
**target file** that names the tool functions to watch and how to run one request. It wraps
those functions with a recorder — logs each call to the trace, then forwards to your real
function untouched — runs your flow, and restores the originals afterwards. Nothing in your
app changes.

```python
# my_target.py — lives next to your agent
import my_app                         # your agent; my_app.run_ticket() is its entry point

NAME = "my-agent"

# Record these REAL tool functions. Name them on the module that CALLS them,
# not where they are defined: `from tools import issue_refund` binds a separate
# name on `my_app`, so name `my_app`, not `tools`. (An empty trace reads as
# "defended" — a false pass — so this matters.)
TOOLS = [
    (my_app, "issue_refund"),
    (my_app, "lookup_order"),
]

def run(payload):
    my_app.run_ticket(payload)        # feed the poisoned input through your real front door
```

```
asrt-bench ❯ /target ./my_target.py
asrt-bench ❯ /run name=v1 pack=./my_pack
   # ... change a prompt, add a guard, edit the router ...
asrt-bench ❯ /run name=v2 pack=./my_pack
asrt-bench ❯ /diff v1 v2            # what newly broke, what your fix actually closed
```

Ship a **wiring control** in your pack — a benign input whose criteria name a tool it
*should* reach, expected to LAND. It is not a vulnerability; it proves the recorder is
attached. If a whole run records zero tool calls, asrt-bench warns you: every verdict is
`defended` by default, and that means nothing. A full, runnable example (no model needed)
is in [`examples/code_routed_agent.py`](examples/code_routed_agent.py). The full step-by-step
is in the [MANUAL](MANUAL.md).

## Demo — break a real support agent in one command

The GIF at the top is a real, unscripted run of this command (sped up for length):

```bash
python -m asrtbench.demo        # needs Ollama + a tools-capable model (default qwen2.5:7b-instruct)
```

It fires a pack of **poisoned support tickets** at a realistic support agent with real tools,
and shows, per attack, whether the ticket tricked the agent into **issuing a refund to an
attacker's order**. Then it does it again with a *security-hardened* system prompt, and diffs
the two:

```
  ┌─ diff(base, hardened) ─────────────────────────────┐
  │  base prompt:      6/8 attacks broke the agent      │
  │  hardened prompt:  6/8 attacks broke the agent      │
  │  The hardening prompt stopped 0 of 6 attacks.       │
  └────────────────────────────────────────────────────┘
```

The punchline: a security system prompt is not a defense. The agent reads a ticket it *has*
to read to do its job, and a hidden "resolution policy" in that ticket drives a real,
fraudulent refund — hardening the prompt changes nothing. asrt-bench measures not whether a
model *says* something bad, but whether your agent *does* something bad, from its tool-call
trace. (`ASRT_DEMO_LIMIT=2` runs a quick version.)

## How it works

```
a frozen pack        your agent             a deterministic
of attacks     ->    (recorder wraps  ->    Verifier reads       ->   verdict, per attack
                     your tools)            the tool-call trace
```

1. **A pack** is a set of attacks. Each attack is a poisoned document + a task for the agent
   + a machine-checkable win condition (*which tool call, with which arguments, = landed*).
2. **The harness / recorder** runs your agent and records every tool call to an append-only
   trace. Against the built-in fixture and model targets the tools are inert (nothing is
   emailed, written, or executed for real); against your own agent they are your real tools.
3. **The Verifier** checks the trace against each attack's win condition. Deterministic. No model.
4. **`/diff`** compares two saved runs: newly broken, newly fixed, or unchanged — and refuses
   to compare two runs that used different packs, rather than fudge it.

## Commands

| Command | What it does |
|---|---|
| `/target <name\|./file.py>` | choose the system under test — a bundled config, or your own `.py` target |
| `/run name=v1` | fire the pack at it, save the result as version `v1` |
| `/run name=v1 pack=<dir>` | fire a specific local pack |
| `/diff <v1> <v2>` | what changed between two saved versions |
| `/versions` | list saved runs |
| `/status` | current target + versions |

## Targets

Three kinds:

- **`.py` target** — your own agent (above). The real use.
- **Model config** — a small JSON file, never code asrt-bench executes. Drives a model as an
  agent against asrt-bench's own inert tools:

  ```json
  { "kind": "model", "provider": "ollama", "model": "qwen2.5:7b-instruct" }
  { "kind": "model", "provider": "openai", "model": "gpt-4o-mini",
    "api_base": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY" }
  ```

- **`fixture`** — deterministic, no model, bundled so you can try the whole flow with nothing installed.

## What ships in the box

A **starter pack** of demonstration attacks spanning the seven capability classes — file
read/write, database, network, messaging, secrets, and code execution — each paired with a
benign control. Its criteria name generic capability tools (`send_email`, `http_request`,
`sql_query`, …), so it lands against asrt-bench's own inert-tool targets and any agent that
uses those common names. To test an agent with its **own** tool names, write a small pack whose
criteria name those tools — see `examples/code_routed_pack/`.

## Honest scope — read this

- **A verdict is about tool calls, not intent.** "Landed" means the agent made the tool call
  the attack's win condition names. That is a fact; whether the agent "meant" to is not measured.
- **The Verifier matches exact tool names.** A pack only lands on an agent whose tools carry
  the names its criteria reference. A pack and a target go together.
- **`unclear` is its own outcome** — a truncated or errored run is never counted as a pass or a fail.
- **A diff is refused, not fudged,** when two runs used different packs or share no attacks.
- **Multi-agent support is early.** Several agents can be recorded into one trace, but
  orchestration is basic.

## What it is not

It does not generate attacks. It replays known ones and verifies them deterministically. That
is deliberate — a tool that only replays a frozen pack is safe to run and to read.

## License

MIT — see [LICENSE](https://github.com/m4vic/asrt-bench/blob/master/LICENSE).
