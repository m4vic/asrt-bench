"""Attach asrt-bench to YOUR agent -- the module that makes "bring your own
target" a 3-line integration instead of copy-pasting the demo.

Every agentic system, from one bot to a multi-agent orchestrator, reduces to
three parts: an INPUT (where untrusted content enters), a BRAIN (model +
persistent prompt that decides what to do), and TOOLS (functions the brain can
call that DO something). To test yours, you do exactly one thing: put a recorder
between the brain and the tools. asrt-bench cannot see inside a program it did
not build -- the recorder is the only way to make your tool calls observable,
and this module makes adding it as small as possible.

    from asrtbench.attach import attach

    target = attach(
        chat_fn=my_model_call,              # (messages, tool_schemas) -> reply
        tools=my_app,                       # object whose methods are your real tools
        tool_schemas=MY_TOOL_SCHEMAS,        # what you expose to the model
        inject=lambda payload: my_app.load_ticket("4821", payload),  # where untrusted input enters
        system_prompt=MY_SYSTEM_PROMPT,
    )

    asrt-bench > /target ./my_target.py
    asrt-bench > /run name=v1
    #  ... change something in your system ...
    asrt-bench > /run name=v2
    asrt-bench > /diff v1 v2        # did the change make it worse?

For a multi-agent system, call `attach()` once per agent whose tools you want
observed (each agent's tools wrapped separately) and pass all of them to
`attach_many()` -- every call still lands in ONE trace, tagged by which agent
made it, so a cross-agent failure ("agent A's output became agent B's argument")
is visible in a single run.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from asrtbench.core import Trace
from asrtbench.harness import drive_tool_loop


class ToolBudgetExceeded(RuntimeError):
    pass


class _Recorder:
    """Wraps a real tools object: records every call + result to a Trace, then
    forwards to the real implementation. This is the entire "record module" --
    nothing here inspects what a tool does, only that it was called and with
    what arguments, which is all a deterministic Verifier needs."""

    def __init__(self, trace: Trace, real_tools: Any, max_tool_calls: int, actor: str) -> None:
        self._trace, self._real, self._max, self._n, self._actor = trace, real_tools, max_tool_calls, 0, actor

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        fn: Callable[..., Any] | None = getattr(self._real, name, None)

        async def _recorded(**kwargs: Any) -> Any:
            self._n += 1
            self._trace.append("tool_call", source=self._actor, data={"tool": name, "args": kwargs})
            if self._n > self._max:
                self._trace.append("budget_exhausted", source="harness", data={"tool": name})
                raise ToolBudgetExceeded(name)
            if fn is None:
                result: Any = {"error": f"unknown tool: {name}"}
            else:
                result = fn(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
            self._trace.append("tool_result", source="app.tool", data={"tool": name, "ok": True})
            return result

        return _recorded


ChatFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[Any]]
InjectFn = Callable[[str], None]


@dataclass
class AttachedTarget:
    """The result of `attach()` -- a real, runnable target for YOUR system.

    `run()` drives one attack: injects the poisoned payload via your `inject`
    function, runs your agent loop against your real (recorded) tools, and
    returns the Trace for asrt-bench's Verifier to check against the attack's
    `success_criteria`. This is exactly the contract asrt-bench's prebuilt packs
    already use -- your target just plugs into the same pipeline.
    """

    name: str
    chat_fn: ChatFn
    tool_schemas: list[dict[str, Any]]
    inject: InjectFn
    system_prompt: str
    real_tools: Any
    actor: str = "agent"
    max_tool_calls: int = 8

    async def run(self, task: str, poisoned_payload: str) -> Trace:
        self.inject(poisoned_payload)
        trace = Trace()
        trace.append("run_started", source="harness", data={"task": task})
        tools = _Recorder(trace, self.real_tools, self.max_tool_calls, self.actor)
        messages = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": task}]
        try:
            await drive_tool_loop(self.chat_fn, messages, tools, tool_schemas=self.tool_schemas)
            trace.append("run_finished", source="harness", data={"error": None})
        except ToolBudgetExceeded as exc:
            trace.append("run_finished", source="harness", data={"error": str(exc)})
        return trace


def attach(
    *,
    chat_fn: ChatFn,
    tools: Any,
    tool_schemas: list[dict[str, Any]],
    inject: InjectFn,
    system_prompt: str,
    name: str = "my-agent",
    actor: str = "agent",
    max_tool_calls: int = 8,
) -> AttachedTarget:
    """Attach asrt-bench to your real agent. See module docstring for the shape.

    - `chat_fn`: your model call, `(messages, tool_schemas) -> {"content", "tool_calls"}`.
      If your framework already has this shape (most do), pass it directly.
    - `tools`: any object whose methods ARE your real tools (issue_refund, etc.) --
      not stubs. The recorder wraps them; it never replaces your logic.
    - `inject`: how a poisoned attack payload enters YOUR system (write to a
      ticket store, append to a document, whatever your real input path is).
    - `actor`: for a multi-agent system, the name of the agent these tools
      belong to -- lets one trace show which agent made which call.
    """
    return AttachedTarget(name=name, chat_fn=chat_fn, tool_schemas=tool_schemas, inject=inject,
                          system_prompt=system_prompt, real_tools=tools, actor=actor,
                          max_tool_calls=max_tool_calls)


def attach_many(*targets: AttachedTarget, name: str = "multi-agent-system") -> "MultiAgentTarget":
    """Combine several `attach()`'d agents into one multi-agent system under
    test. Each keeps its own actor tag; all calls land in one shared Trace, so a
    cross-agent handoff (poisoned data crossing from one agent's read into
    another agent's tool call) is visible in a single run."""
    return MultiAgentTarget(name=name, members=list(targets))


@dataclass
class MultiAgentTarget:
    name: str
    members: list[AttachedTarget] = field(default_factory=list)

    async def run(self, task: str, poisoned_payload: str) -> Trace:
        """Runs each member's turn in order against ONE shared trace. A real
        orchestrator would decide the order/handoffs itself; for a first pass,
        each member is driven once and their tool calls all land in the same
        trace, tagged by actor, so cross-agent misuse is still observable."""
        trace = Trace()
        trace.append("run_started", source="harness", data={"task": task})
        for member in self.members:
            member.inject(poisoned_payload)
            tools = _Recorder(trace, member.real_tools, member.max_tool_calls, member.actor)
            messages = [{"role": "system", "content": member.system_prompt},
                       {"role": "user", "content": task}]
            try:
                await drive_tool_loop(member.chat_fn, messages, tools, tool_schemas=member.tool_schemas)
            except ToolBudgetExceeded:
                pass
        trace.append("run_finished", source="harness", data={"error": None})
        return trace
