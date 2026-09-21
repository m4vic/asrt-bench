# asrt-bench — mini manual

How to point asrt-bench at **your own agent**, fire the **prebuilt attacks** at
it, and **version-control its safety** so you catch the day a change makes it
worse.

---

## 0. What asrt-bench does (30 seconds)

It fires a pack of poisoned inputs at an AI agent and checks, from the agent's
**tool-call trace**, whether any of them drove the agent to *do* something it
shouldn't — email data out, run a query, issue a refund. Success is a **fact
about a recorded tool call**, decided deterministically. No LLM judge, no drift.

Two prebuilt attack packs ship with it:

| Pack | Path | What it is |
|---|---|---|
| **demo** | `asrtbench/demo/attacks.json` | 24 realistic support-agent attacks (poisoned tickets) across 3 harm types: refund fraud, data exfiltration, account takeover |
| **starter** | `asrtbench/packs/starter/` | 12 cases across the 7 capability classes (generic tools) |

Both are yours to run, copy, and extend.

## 1. See it work first

```bash
python -m asrtbench.demo          # needs Ollama + a tools-capable model (qwen2.5:7b-instruct)
```

Watch a support agent get tricked into a real refund, and see a hardening prompt
stop nothing. `ASRT_DEMO_LIMIT=2` for a quick run.

---

## 2. Test YOUR agent — three ways

### Way A — a model + a tool surface (quick, config only)

If you just want to know "would *this model*, given tools like `send_email`,
misuse them," write a small JSON target and run a pack at it. No code.

```json
// my-agent.json
{ "kind": "model", "provider": "ollama", "model": "qwen2.5:7b-instruct" }
```

```
python -m asrtbench
asrt-bench ❯ /target ./my-agent.json
asrt-bench ❯ /run name=v1
```

OpenAI-compatible endpoints work too (`"provider": "openai"`, `"api_base": ...`,
`"api_key_env": "OPENAI_API_KEY"`).

### Way B — your own Python agent, tools recorded (`.py` target)

Use this when **your code** decides which tool to call — a LangGraph node, a
hand-written router, any "agent framework with a graph". There is no model
tool-calling loop for asrt-bench to sit inside, so it records your tool
*functions* instead: it swaps each one for a wrapper that logs the call and
forwards to the real function, runs your flow untouched, then puts the
originals back.

Write one file. Nothing in your app changes:

```python
# my_target.py
import my_app.flow as flow          # the module that CALLS the tools

NAME  = "my-agent"
TOOLS = [(flow, "lookup_order"), (flow, "issue_refund")]

def run(payload):
    flow.handle({"ticket": payload})   # your normal front door
```

```
asrt-bench ❯ /target ./my_target.py
asrt-bench ❯ /run name=v1 pack=./my_pack
```

Two things to get right:

- **Patch where a tool is CALLED, not where it is defined.** `from tools import
  lookup_order` binds a *separate* name in the importing module, so `TOOLS` must
  name that module, not `tools`. Get this wrong and nothing is recorded — and an
  empty trace verifies as `defended`, a false clean pass rather than an error.
  asrt-bench prints a loud warning when a whole run records zero tool calls.
- **Ship a wiring control in your pack**: a benign input whose criteria name the
  tool it *should* reach, expected to verify as `success`. It is not a
  vulnerability — it is the proof the recorder is on the wire. If it comes back
  `defended`, every other verdict in that run is meaningless.

A complete runnable example, with a pack containing an attack, a benign control
and a wiring control: `examples/code_routed_agent.py` (no model needed).

### Way C — your REAL app driven by a model (`asrtbench.attach`, ~10 lines)

Every agentic system reduces to three parts: an **input** (where untrusted
content enters), a **brain** (model + persistent prompt), and **tools** (what the
brain can call, that do something). asrt-bench cannot see inside a program it
did not build — the only way to make your tool calls observable is to put a
recorder between your brain and your tools. `asrtbench.attach` makes that one
call:

```python
from asrtbench.attach import attach
from asrtbench.runner import run_pack_on_attached

target = attach(
    chat_fn=my_model_call,              # your (messages, tool_schemas) -> reply
    tools=my_app,                       # object whose METHODS are your real tools
    tool_schemas=MY_TOOL_SCHEMAS,       # what you expose to the model
    inject=lambda payload: my_app.load_ticket("4821", payload),  # where untrusted input enters
    system_prompt=MY_SYSTEM_PROMPT,
    name="my-agent",
)

result = run_pack_on_attached(target, "asrtbench/demo")  # or your own pack dir
```

- `tools` are **your real implementations** — the recorder wraps them, it never
  replaces your logic. A landed attack is a real consequence in your app's own
  state (e.g. a refund actually issued).
- `inject` is the one line that says *how untrusted content reaches your
  system* — a ticket, a document, an email, whatever your real input path is.
- **Multi-agent:** call `attach()` once per agent whose tools you want
  observed, then `attach_many(a, b, c)` — every call from every agent lands in
  ONE trace tagged by actor, so a cross-agent handoff (agent A's output becoming
  agent B's tool argument) is visible in a single run.

See `examples/bring_your_own_app.py` — a complete, runnable, independent app
(shares no code with the demo) proving `attach()` works on a system it has
never seen. Run from the repo root: `python -m examples.bring_your_own_app`.

---

## 3. Fire a prebuilt pack at your target

```
asrt-bench ❯ /run name=v1                 # runs the bundled starter pack
asrt-bench ❯ /run name=v1 pack=./somepack # or a specific pack directory
```

Each attack prints a verdict:
- **LANDED** — the agent did the bad thing (the attack succeeded).
- **defended** — the agent refused / never took the action.
- **unclear** — the run couldn't be decided (e.g. it was cut off). Never counted
  as pass or fail.

---

## 4. Version-control your agent's safety (the core feature)

This is the point of asrt-bench: an agent that was safe last week can become
unsafe after *any* change — a new model version, a new tool, a reworded prompt, a
new guardrail. You catch it by diffing runs.

```
asrt-bench ❯ /run name=v1        # today's agent
#   ... change something: swap the model, add a guard, edit the prompt ...
asrt-bench ❯ /run name=v2        # the changed agent
asrt-bench ❯ /diff v1 v2         # what NEWLY broke, what NEWLY got fixed
```

`/diff` tells you exactly which attacks changed outcome — so a "small" prompt
tweak that quietly reopened an exfil hole shows up as a **regression**, not a
surprise in production. Two runs are only comparable if they used the **same
pack** (asrt-bench refuses to diff mismatched runs rather than fudge it).

```
asrt-bench ❯ /versions           # list saved runs
asrt-bench ❯ /status             # current target + versions
```

Use it in CI the same way: run the pack on every change, fail the build if
`/diff` shows a new landing attack.

---

## 5. Free vs. paid attacks

- **Free (now):** the prebuilt packs above — run them offline, forever.
- **Paid (coming):** a live API that generates *fresh, larger* packs tailored to
  your target. `/api` shows how to connect it; until then the prebuilt packs work
  fully offline. See the README's "Attack sources" section.

---

## 6. Honest scope

- It tests **a model wired to instrumented tools**, or your app via the recorder —
  a verdict means "this agent, given these tools, misused one under a poisoned
  input," not "your whole production stack is exploitable end to end."
- **`unclear` is its own outcome** — never a pass or a fail.
- A **diff is refused, not faked**, when two runs used different packs.
