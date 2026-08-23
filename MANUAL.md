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
| **demo** | `asrtbench/demo/attacks.json` | 8 realistic support-agent attacks (poisoned tickets) |
| **starter** | `asrtbench/packs/starter/` | 8 cases across the 7 capability classes (generic tools) |

Both are yours to run, copy, and extend.

## 1. See it work first

```bash
python -m asrtbench.demo          # needs Ollama + a tools-capable model (qwen2.5:7b-instruct)
```

Watch a support agent get tricked into a real refund, and see a hardening prompt
stop nothing. `ASRT_DEMO_LIMIT=2` for a quick run.

---

## 2. Test YOUR agent — two ways

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

### Way B — your REAL app (the honest way, ~20 lines)

To test your *actual* agent with *its own* tools, wrap your tool dispatch with
the recorder. This records every call to a Trace asrt-bench can verify, then
forwards to your real tool. Use **`asrtbench/demo/app.py` as the template** — it
does exactly this for the support app. The shape:

```python
from asrtbench.core import Trace
from asrtbench.harness import drive_tool_loop

trace = Trace()
trace.append("run_started", source="harness", data={"task": task})

# `tools` is any object whose attributes are your real tool functions.
# The recorder wraps them: records the call, then calls your real function.
tools = RecordingToolset(trace, your_app, max_tool_calls=8)   # copy RecordingToolset from demo/app.py

await drive_tool_loop(your_chat_fn, messages, tools, tool_schemas=YOUR_TOOL_SCHEMAS)
trace.append("run_finished", source="harness", data={"error": None})

# then verify against an attack's success_criteria:
from asrtbench.adjudication import Verifier
verdict = Verifier().verify(attack["success_criteria"], trace, bindings={"canary": canary})
```

You declare **your** tools (`tool_schemas`), the agent calls **your** real
implementations, and asrt-bench reads the trace. This is how you'd attach it to a
production agent — you wrap the dispatch, you don't replace the tools.

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
