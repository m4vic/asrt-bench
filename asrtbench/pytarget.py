"""Test a Python agent that routes its own tool calls.

`asrtbench.attach` assumes asrt-bench drives the model's tool-calling loop: it
sends the model a message, the model answers "call send_email", and asrt-bench
makes that call. Many real agents are not shaped that way. A LangGraph node, or
any hand-written router, decides which tool to call in *your* code and calls it
directly -- there is no loop for asrt-bench to sit inside.

This module records the tool FUNCTIONS instead. You point it at a Python file
naming your tools and how to run one request; asrt-bench swaps each named
function for a wrapper that logs the call and forwards to the real one, runs
your flow untouched, then puts the originals back. Nothing in your app changes.

A target file needs three names:

    import my_app.flow as flow          # wherever your tools are USED

    NAME  = "my-agent"                  # optional, defaults to the filename
    TOOLS = [(flow, "lookup_order"), (flow, "issue_refund")]

    def run(payload):
        flow.handle({"ticket": payload})

Patch where a function is USED, not where it is defined. `from tools import
lookup_order` binds a *separate* name in the importing module, so replacing
`tools.lookup_order` leaves `flow.lookup_order` pointing at the original and
records nothing. `TOOLS` is validated at load time to catch a name that does not
exist, but it cannot detect a name that exists in the wrong module -- that is
what the pack's wiring control is for.

No tool-call budget applies here, deliberately. `InstrumentedTools` raises
`ToolBudgetExceeded` to stop a model looping forever; your flow terminates on
its own, and raising inside it would be swallowed by your own `except` blocks,
producing a silently wrong run. Recording `budget_exhausted` without actually
stopping anything would fake an `unclear` verdict, which is worse than no limit.

Diagram box: TARGET ADAPTER + RECORDER, for your own Python agent.
Not the TARGET or TOOLS boxes: those are your code, and no file here implements them.
Full box -> file map: docs/modules_keywords.md
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from asrtbench.core import Trace, TargetProfile


class PythonTargetError(RuntimeError):
    """A target file could not be loaded, or does not declare what it must.

    Raised at load time on purpose: a missing `run`, or a tool name that does not
    exist, must stop the run loudly. The silent version of this mistake is an
    empty Trace, which the Verifier reads as `defended` -- a false clean pass.
    """


def _arguments_as_dict(function: Callable, args: tuple, kwargs: dict) -> dict[str, Any]:
    """Pair every argument with its parameter name.

    An attack's success criteria name their arguments (`args.order_id`), but your
    code is free to pass them positionally -- `update_account("a-1", "email")`.
    `signature().bind()` matches values to names the same way Python does when it
    calls the function, so a positional call still yields evidence the Verifier
    can match. Defaults are filled in for the same reason: a criterion on an
    argument the caller omitted should see the value the function actually used.
    """
    try:
        bound = inspect.signature(function).bind(*args, **kwargs)
    except (TypeError, ValueError):
        # Builtins and some decorated functions expose no bindable signature.
        # Record the raw values rather than nothing -- unnamed evidence still
        # proves the call happened, and an empty trace reads as `defended`.
        return {"args": list(args), **kwargs}
    bound.apply_defaults()
    return dict(bound.arguments)


def _record_call(trace: Trace, tool_name: str, real_function: Callable, actor: str) -> Callable:
    """Build a stand-in for one tool function: log the call, then forward it.

    This wrapper is the entire reason asrt-bench can see anything. It cannot look
    inside a program it did not build, so an unwrapped tool leaves no evidence at
    all. The real function receives the caller's original arguments and its
    result is returned unchanged -- recording must never alter behaviour, or the
    run stops being a measurement of your agent.

    Returns an async wrapper for an async tool, so the result is recorded after
    the call truly completes rather than when the coroutine was created.
    """

    def log_call(args: tuple, kwargs: dict) -> None:
        trace.append(
            "tool_call",
            source=actor,
            data={"tool": tool_name, "args": _arguments_as_dict(real_function, args, kwargs)},
        )

    def log_result(ok: bool) -> None:
        trace.append("tool_result", source=actor, data={"tool": tool_name, "ok": ok})

    if inspect.iscoroutinefunction(real_function):

        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            log_call(args, kwargs)
            try:
                result = await real_function(*args, **kwargs)
            except Exception:
                # A failed tool call is still a fact about the run. Record it,
                # then re-raise bare so your app's own error handling sees the
                # original exception and traceback, unchanged.
                log_result(False)
                raise
            log_result(True)
            return result

        return async_wrapper

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        log_call(args, kwargs)
        try:
            result = real_function(*args, **kwargs)
        except Exception:
            log_result(False)
            raise
        log_result(True)
        return result

    return wrapper


@dataclass
class PythonTarget:
    """Your own Python agent, with its tool functions recorded while it runs.

    Produces the same `Trace` as every other target, so the Verifier, the store
    and `/diff` treat it identically -- see `runner.run_pack`.
    """

    name: str
    run_function: Callable[[str], Any]
    # (owner, attribute_name) pairs. `owner` is whatever object holds the tool --
    # normally the module that calls it. Validated at load time.
    tools: list[tuple[Any, str]] = field(default_factory=list)
    actor: str = "app.tool"

    def tool_names(self) -> list[str]:
        return [name for _owner, name in self.tools]

    def _install(self, trace: Trace) -> list[tuple[Any, str, Callable]]:
        """Swap each declared tool for a recording wrapper.

        Returns what was replaced so `_restore` can put it back. Restoring is not
        optional: these objects are module-level and outlive one attack, so a
        wrapper left in place would keep writing into a finished Trace and leak
        across every later case in the run.
        """
        originals: list[tuple[Any, str, Callable]] = []
        for owner, attribute in self.tools:
            real_function = getattr(owner, attribute)
            originals.append((owner, attribute, real_function))
            setattr(owner, attribute, _record_call(trace, attribute, real_function, self.actor))
        return originals

    @staticmethod
    def _restore(originals: list[tuple[Any, str, Callable]]) -> None:
        for owner, attribute, real_function in originals:
            setattr(owner, attribute, real_function)

    def run_case(self, task: str, fixtures: dict[str, str]) -> Trace:
        """Run one attack through your flow and return what your tools did.

        Only the first fixture value is used: a `.py` target's `run()` takes one
        poisoned payload, the same convention `attach()` uses. Multi-file
        fixtures are a harness concept and have no meaning for your own input path.
        """
        trace = Trace(target=self.name)
        trace.append("run_started", source="harness", data={"task": task})

        payload = next(iter(fixtures.values()), "")
        originals = self._install(trace)
        try:
            outcome = self.run_function(payload)
            if inspect.iscoroutine(outcome):
                asyncio.run(outcome)
            error = None
        except Exception as exc:
            # Your agent crashing is a fact about this run, not an asrt-bench
            # failure. Recording it makes the run incomplete, so the verdict
            # reads `unclear` rather than a false `defended`.
            error = f"{type(exc).__name__}: {exc}"
        finally:
            self._restore(originals)

        trace.append("run_finished", source="harness", data={"error": error})
        return trace

    def profile(self) -> TargetProfile:
        return TargetProfile.from_tools(self.name, self.tool_names())

    def describe(self) -> dict[str, Any]:
        p = self.profile()
        return {
            "name": self.name,
            "kind": "python",
            "provider": None,
            "model": None,
            "endpoint": None,
            # Your flow decides what runs; asrt-bench only observes. Whether a
            # result repeats depends on your agent, not on asrt-bench.
            "deterministic": False,
            "tools": self.tool_names(),
            "capabilities": sorted(p.capabilities),
            "blast_ceiling": p.blast_ceiling(),
            "attacker_knowledge": "schema",
        }


def _import_file(path: Path) -> Any:
    """Import a .py file by path, without it needing to be an installed package.

    The file's own folder goes on `sys.path` first so a target file can sit next
    to the app it tests and use ordinary imports (`from ticket import workflow`).
    """
    folder = str(path.parent.resolve())
    if folder not in sys.path:
        sys.path.insert(0, folder)

    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise PythonTargetError(f"{path}: not importable as Python")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution so anything inside that looks itself up by
    # name (dataclasses, pickle, some decorators) resolves to this module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_python_target(path: str | Path) -> PythonTarget:
    """Load a `.py` target file. See the module docstring for its three names.

    Every declared tool is checked here. A typo in `TOOLS` that slipped through
    would record nothing and hand back an empty Trace, which the Verifier scores
    as `defended` -- so the check is a hard error, not a warning.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no target file at {path}")

    module = _import_file(path)

    run_function = getattr(module, "run", None)
    if not callable(run_function):
        raise PythonTargetError(
            f"{path}: needs a `run(payload)` function -- the one call that feeds "
            "a poisoned input through your agent"
        )

    declared = getattr(module, "TOOLS", [])
    tools: list[tuple[Any, str]] = []
    for entry in declared:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise PythonTargetError(
                f"{path}: each TOOLS entry must be (module, \"function_name\"), got {entry!r}"
            )
        owner, attribute = entry
        if not isinstance(attribute, str):
            raise PythonTargetError(f"{path}: tool name must be a string, got {attribute!r}")
        target_function = getattr(owner, attribute, None)
        if not callable(target_function):
            raise PythonTargetError(
                f"{path}: {getattr(owner, '__name__', owner)!r} has no callable {attribute!r}"
            )
        tools.append((owner, attribute))

    if not tools:
        raise PythonTargetError(
            f"{path}: TOOLS is empty -- with nothing recorded every attack would "
            "verify as `defended` whatever your agent did"
        )

    return PythonTarget(name=getattr(module, "NAME", path.stem), run_function=run_function, tools=tools)
