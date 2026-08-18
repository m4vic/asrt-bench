# asrt-bench

**Fire a frozen attack pack at your AI agent, verify what lands, and diff safety across versions.**

[![License: MIT](https://img.shields.io/github/license/m4vic/asrt-bench?color=22d3ee)](LICENSE)
[![Stars](https://img.shields.io/github/stars/m4vic/asrt-bench?style=flat&color=22d3ee)](https://github.com/m4vic/asrt-bench/stargazers)
[![Python](https://img.shields.io/badge/python-3.10%2B-22d3ee.svg)](requirements.txt)

<p align="center">
  <img src="docs/screenshot.png" alt="asrt-bench console" width="820">
</p>

> **This project is constantly evolving and is currently not production-grade.**
> 
asrt-bench answers one question, and answers it without a human or an LLM judge:

> Did a poisoned input drive your agent to misuse a tool — and did your last change make that better or worse?

Success is a **fact about a recorded tool call**, not an opinion about text. When the agent
calls `send_email` to an attacker domain with your secret in the body, that call is in the
trace or it isn't. No grader, no threshold, no drift.

## Install (clone and run)

clone it and run it:

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

Requires Python 3.10+. Run the tests with `pip install -r requirements-dev.txt && pytest`.

## How it works

```
a frozen pack        an agent               a deterministic
of attacks     ->    (your model +    ->    Verifier reads       ->   verdict, per attack
                     inert tools)           the tool-call trace
```

1. **A pack** is a set of attacks. Each attack is a poisoned document + a task for the agent
   + a machine-checkable win condition (*which tool call, with which arguments, = landed*).
2. **The harness** runs your model as an agent with inert, instrumented tools — nothing is
   emailed, written, queried, or executed for real, but every attempt is recorded.
3. **The Verifier** checks the trace against each attack's win condition. Deterministic. No model.
4. **`/diff`** compares two saved runs: newly broken, newly fixed, or unchanged.

## Commands

| Command | What it does |
|---|---|
| `/target <name>` | choose the system under test (`/target list`) |
| `/run name=v1` | fire the pack at it, save the result as version `v1` |
| `/run name=v1 pack=<dir>` | fire a specific local pack |
| `/diff <v1> <v2>` | what changed between two saved versions |
| `/versions` | list saved runs |
| `/status` | current target + versions |

## Targets

A target is a small JSON config — never code asrt-bench executes:

```json
{ "kind": "model", "provider": "ollama", "model": "qwen2.5:7b-instruct" }
```

```json
{ "kind": "model", "provider": "openai", "model": "gpt-4o-mini",
  "api_base": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY" }
```

Local (Ollama) or any OpenAI-compatible endpoint. A `fixture` target (deterministic, no model)
is bundled so you can try the whole flow with nothing installed.

The `tools` field narrows the capability surface a target exposes:

```json
{ "kind": "model", "provider": "ollama", "model": "qwen2.5:7b-instruct",
  "tools": ["read_file", "send_email"] }
```

## What ships in the box

A **starter pack** of demonstration attacks spanning the seven capability classes —
file read/write, database, network, messaging, secrets, and code execution — each paired
with a benign control. It is a *demo*, enough to see the tool work end to end. Larger, real
attack packs are published separately.

## Honest scope — read this

- **It tests a model wired to *our* inert tools, not your deployed application.** The target
  is the model's brain; asrt-bench supplies the tools and the agent loop. So a verdict means
  "would this model, given a tool like `send_email`, misuse it under a poisoned document" —
  **not** "your production system is exploitable end to end." Attaching to a real running agent
  with its own tools is a larger problem this does not solve today.
- **Single-agent only.** One model behind a set of tools. Multi-agent systems (an orchestrator
  with sub-agents) are out of scope.
- **The tool subset is advisory for model targets.** A model target's `tools` field shapes the
  reported capability surface, but the harness still offers the model all instrumented tools.
- **`unclear` is its own outcome** — never counted as a pass or a fail.
- **A diff is refused, not fudged,** when two runs used different packs or share no attacks.
  A comparison it cannot honestly make, it declines.

## What it is not

It does not generate attacks. It replays known ones and verifies them. That is deliberate —
a tool that can only replay a frozen pack is safe to run and to read. Attack *generation* is a
separate, private engine.

## License

MIT — see [LICENSE](LICENSE).
