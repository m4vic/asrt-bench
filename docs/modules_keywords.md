# Diagram box → file map

Which files actually implement each box in the system diagram. Written to answer
"the attack pack — which file is that?" without grepping.

Line numbers are a snapshot (2026-09-21) and will drift; the file and symbol names are
the durable part.

---

## ATTACK PACK — the input `[cylinder]`

An attack is **data on disk**, so this box is two things: the JSON files, and the code
that turns them into objects.

| What | Where |
|---|---|
| The data itself | `asrtbench/packs/starter/*.json`, `asrtbench/demo/attacks.json`, `examples/code_routed_pack/cases.json` |
| Parses one JSON record into an object | `core/attack.py` → `AttackCase`, `AttackCase.from_raw`, `AttackCase.load_file` |
| Reads a whole pack directory | `runner.py:48` → `load_pack()`, `runner.py:56` → `starter_pack_dir()` |

`load_pack` keeps only `channel == "action"` cases. A pack file with text-channel entries
silently contributes nothing, which is intended — the text judge is not part of this tool.

## PACK FINGERPRINT — `pack_hash` `[parallelogram]`

| What | Where |
|---|---|
| Hashes attack *content*, not just ids | `core/suite.py:40` → `Suite`, `Suite.freeze` (line 48), `_fingerprint`, `_digest` |
| Called once per run | `runner.py` inside `run_pack` |
| Read by the diff gate | `diff.py:64` → `compare()` |

Change a criterion in a pack file and this hash changes, which is what makes two runs
provably the same measurement or provably not.

## TARGET — the thing under test `[dashed = YOURS]`

**No asrt-bench file implements this box.** It is your agent: your router, your prompts,
your model calls. For `ai-ops-platform` it is `yours/ticket.py` — the graph, the nodes and
`route_by_category`. asrt-bench never replaces or modifies any of it.

## TARGET ADAPTER — asrt-bench's handle on your target `[solid]`

This is the box the files below really belong to, and it is easy to confuse with TARGET.
The adapter does not run your logic; it knows three things about it: what to call it, how
to feed it one poisoned input, and which tool functions to record.

| Kind | Where | Its job |
|---|---|---|
| Your Python agent | `pytarget.py:135` → `PythonTarget` | reads `NAME` / `TOOLS` / `run()` from your `.py` file, patches, calls `run(payload)`, restores |
| Your model-driven app | `attach.py:87` → `AttachedTarget` | injects the payload, drives the model's tool loop against your real tools |
| Several of yours at once | `attach.py` → `MultiAgentTarget` | one shared Trace, tagged per agent |
| **No external system at all** | `target.py:51` → `Target` | the odd one out — see below |
| Picks which adapter | `target.py:224` → `load_target(ref)` | `.py` path → `PythonTarget`, otherwise JSON → `Target` |
| The contract all of them meet | `runner.py:36` → `PackTarget` (Protocol) | `name` + `run_case(task, fixtures) -> Trace` |

`Target` (the JSON kind) is genuinely different from the other two: there is no external
system. asrt-bench supplies the agent *and* the tools, and only the model is outside. It
answers "would this model, handed a tool shaped like `send_email`, misuse it" — not "is my
app exploitable". That is a different diagram from the one you drew.

Bundled config files: `asrtbench/targets/*.json`.

## TOOLS `[dashed = YOURS]`

**No asrt-bench file implements this box either.** These are your real functions. For
`ai-ops-platform`: `yours/tools.py` → `lookup_order`, `search_kb`, `update_account`. The
recorder wraps them; it never replaces them, so a landed attack is a real consequence in
your own system's state.

### Not this box: asrt-bench's inert stand-ins

`harness/action.py:40` → `InstrumentedTools` is a *substitute* toolset — seven fake tools
(`read_file`, `send_email`, `python_exec`, …) that record an attempt and do nothing. They
exist only for the `Target` kind above, where there is no real system to point at. They are
not on your diagram, and they are never used when testing your own app.

Related: `core/capability.py` → `TOOL_TO_CAPABILITY`, `CAPABILITY_WEIGHT` map a tool name
to a severity weight. That applies to *any* tool name, yours included — it is what fills the
"max blast" meter for a `.py` target.

## RECORDER — the box sitting on the wire `[solid = asrt-bench]`

Three implementations, one per target kind. This is the piece that makes tool calls
visible; without it there is no evidence at all.

| For | Where |
|---|---|
| A `.py` target | `pytarget.py:81` → `_record_call()`, `pytarget.py:60` → `_arguments_as_dict()`, `PythonTarget._install` / `._restore` |
| An attached target | `attach.py:50` → `_Recorder` (intercepts attribute access via `__getattr__`) |
| Inert tools | `harness/action.py:146` → `InstrumentedTools._begin_call()` — records straight into the Trace |

`_arguments_as_dict` is why a positional call like `lookup_order("12312")` still records
`{"order_id": "12312"}`, which is what criteria match on.

## TRACE — the evidence log `[cylinder]`

| What | Where |
|---|---|
| The log and its events | `core/trace.py:56` → `Trace`, `core/trace.py:36` → `TraceEvent` |
| The single write path | `Trace.append()` |
| The read paths | `Trace.events` (property), `Trace.completeness()` |
| Makes recorded data un-editable | `core/trace.py` → `_freeze`, `_thaw`, `_frozen_mapping` |
| Save / reload a trace | `Trace.to_dict`, `Trace.from_dict`; used by `evidence/action_store.py` |

## VERIFIER — evidence to verdict `[rectangle]`

| What | Where |
|---|---|
| Walks the criteria tree over the trace | `adjudication/verifier.py:11` → `Verifier.verify()`, `_evaluate`, `_collect_progress`, `_where_matches` |
| The predicate language + its validation | `core/criteria.py:17` → `SuccessCriteria`, `CriteriaValidationError` |
| The output shape | `core/verdict.py:10` → `Verdict` |
| Severity of what was reached | `core/capability.py:87` → `blast_radius()` |

## LANDED / defended / unclear `[three boxes]`

Not a file — the three possible values of `Verdict.value`, decided inside
`verifier.py`'s `verify()`. How they are coloured and labelled: `cli.py` → the `VERDICT`
dict near the top.

## RESULTS — one row per attack `[box]`

| What | Where |
|---|---|
| One attack's outcome | `runner.py:61` → `CaseOutcome` |
| The whole run | `runner.py:72` → `RunResult`, `RunResult.counts()` |

## THE LOOP — runner `[the arrow going back]`

| What | Where |
|---|---|
| Loads the pack, mints canaries, drives every case, verifies | `runner.py:96` → `run_pack()` |
| Mints the per-case canary | `runner.py` → `_canary()` |
| Deprecated alias kept for old scripts | `runner.py:154` → `run_pack_on_attached()` |

## SAVE `[cylinder: runs/*.json]`

| What | Where |
|---|---|
| Write / read / list saved runs | `store.py:31` → `save()`, `:53` → `load()`, `:73` → `list_versions()`, `:67` → `meta()`, `:49` → `exists()` |
| Where they land | `store.py:23` → `store_dir()` — `runs/`, or `$ASRT_BENCH_STORE` |

## DIFF `[the second flow]`

| What | Where |
|---|---|
| Compares two saved runs | `diff.py:64` → `compare()` |
| The report | `diff.py:38` → `DiffReport`, `diff.py` → `AttackChange` |
| The two refusals | `diff.py:26` → `IncomparableRuns` — raised on a pack-hash mismatch and on zero attack overlap |

## CLI — the outer shell

| Command | Where |
|---|---|
| `/target` | `cli.py:70` → `cmd_target` |
| `/run` | `cli.py:121` → `cmd_run` |
| `/diff` | `cli.py:256` → `cmd_diff` |
| `/versions`, `/status`, `/api` | `cli.py:329`, `:345`, `:370` |
| The REPL and arg parsing | `cli.py:428` → `main()`, `cli.py:396` → `parse()` |

The CLI decides nothing about a verdict — it calls the library and renders what comes back.

---

## Reverse index: file → which box

| File | Box |
|---|---|
| `core/attack.py` | ATTACK PACK (parsing) |
| `core/suite.py` | PACK FINGERPRINT |
| `core/trace.py` | TRACE |
| `core/criteria.py` | VERIFIER (the question) |
| `core/verdict.py` | VERIFIER (the answer shape) |
| `core/capability.py` | tool severity weights + VERIFIER (blast radius) |
| `core/target_profile.py` | TARGET ADAPTER (capability summary shown by `/target`) |
| `adjudication/verifier.py` | VERIFIER |
| `harness/action.py` | INERT STAND-IN TOOLS (not your TOOLS box) + their recorder + the run wrapper |
| `harness/model_agent.py` | TARGET ADAPTER (drives a model's tool loop) |
| `harness/targets.py`, `harness/cassette.py` | TARGET ADAPTER (model transport, record/replay) |
| `target.py` | TARGET ADAPTER (the no-external-system kind) + `load_target` dispatcher |
| `pytarget.py` | TARGET ADAPTER (your Python agent) + RECORDER |
| `attach.py` | TARGET ADAPTER (your model-driven app) + RECORDER |
| `runner.py` | THE LOOP + RESULTS |
| `store.py` | SAVE |
| `diff.py` | DIFF |
| `cli.py` | the outer shell |
| `attack_api.py` | ATTACK PACK (fetching a paid pack instead of a local one) |
| `evidence/` | SAVE (the database layer, separate from `store.py`) |

## Not on the diagram

`evidence/` (action_store, bundle, database) is a second persistence layer that stores
full traces in SQLite. `store.py` — the one `/diff` uses — saves only the verdict summary
per run. Two different things with similar names.

`demo/` is a self-contained worked example (its own app, recorder and pack), not part of
the main flow.
