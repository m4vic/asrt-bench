"""Controlled action-channel execution with inert, instrumented tools."""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from asrtbench.core import Trace


# A directive embedded in untrusted text, e.g.
#   [[ACTION {"tool": "send_email", "args": {"to": "x", "body": "y"}}]]
_DIRECTIVE = re.compile(r"\[\[ACTION\s+(\{.*?\})\s*\]\]", re.DOTALL)


class ToolBudgetExceeded(RuntimeError):
    """The agent exceeded the tool-call budget declared for this run."""


class HarnessError(RuntimeError):
    """Infrastructure failure that must halt a run, not be recorded as a verdict.

    Agent misbehaviour (an exception while acting) is legitimately recorded as a
    truncated run. But a broken replay cassette, a missing dependency, or a
    misconfiguration is not a result about the target -- letting it degrade into
    an `unclear` verdict would hide a real failure behind a plausible one.
    """


class ActionAgent(Protocol):
    """The narrow contract a model adapter or deterministic fixture must satisfy."""

    def run(self, task: str, tools: "InstrumentedTools") -> str | Any:
        """Use the supplied tools and return a final response."""


class InstrumentedTools:
    """Inert tools that make every attempted action observable in a Trace."""

    def __init__(self, trace: Trace, fixtures: Mapping[str, str], max_tool_calls: int) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        self._trace = trace
        self._fixtures = dict(fixtures)
        self._max_tool_calls = max_tool_calls
        self._tool_calls = 0

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    async def read_file(self, path: str) -> str:
        self._begin_call("read_file", {"path": path})
        if path not in self._fixtures:
            self._trace.append(
                "tool_result",
                source="harness.tool",
                data={"tool": "read_file", "ok": False, "error": "fixture_not_found"},
            )
            return ""
        content = self._fixtures[path]
        self._trace.append(
            "tool_result",
            source="harness.tool",
            data={"tool": "read_file", "ok": True, "bytes": len(content.encode("utf-8"))},
        )
        return content

    async def send_email(self, to: str, body: str) -> dict[str, str]:
        self._begin_call("send_email", {"to": to, "body": body})
        receipt = {"status": "recorded", "delivery": "suppressed"}
        self._trace.append(
            "tool_result",
            source="harness.tool",
            data={"tool": "send_email", "ok": True, **receipt},
        )
        return receipt

    async def http_request(self, url: str) -> dict[str, str]:
        self._begin_call("http_request", {"url": url})
        response = {"status": "recorded", "network": "disabled"}
        self._trace.append(
            "tool_result",
            source="harness.tool",
            data={"tool": "http_request", "ok": True, **response},
        )
        return response

    # ---- widened surface (P3): the remaining four capability classes ----
    # Each is inert exactly like the three above: the attempt is recorded, a
    # synthetic result is returned, and nothing crosses the boundary -- no file
    # is written, no query runs, no secret is real, no code executes.

    async def write_file(self, path: str, content: str) -> dict[str, str]:
        """local_write. An injection that reaches this can tamper or persist."""
        self._begin_call("write_file", {"path": path, "content": content})
        result = {"status": "recorded", "written": False}
        self._trace.append(
            "tool_result",
            source="harness.tool",
            data={"tool": "write_file", "ok": True, **result},
        )
        return result

    async def sql_query(self, query: str) -> dict[str, Any]:
        """data_store. An injection that reaches this is where LLM meets SQLi."""
        self._begin_call("sql_query", {"query": query})
        result = {"status": "recorded", "executed": False, "rows": []}
        self._trace.append(
            "tool_result",
            source="harness.tool",
            data={"tool": "sql_query", "ok": True, "executed": False},
        )
        return result

    async def read_env(self, key: str) -> str:
        """secrets. Reaching this via untrusted text is itself the finding.

        Returns a synthetic, clearly-fake value so a *chained* attack
        (read a secret, then exfiltrate it) has something to carry -- but the
        value is never a real credential.
        """
        self._begin_call("read_env", {"key": key})
        value = f"SYNTHETIC-SECRET::{key}"
        self._trace.append(
            "tool_result",
            source="harness.tool",
            data={"tool": "read_env", "ok": True, "synthetic": True},
        )
        return value

    async def python_exec(self, code: str) -> dict[str, str]:
        """code_exec. The highest blast radius: arbitrary code via injection."""
        self._begin_call("python_exec", {"code": code})
        result = {"status": "recorded", "executed": False, "stdout": ""}
        self._trace.append(
            "tool_result",
            source="harness.tool",
            data={"tool": "python_exec", "ok": True, "executed": False},
        )
        return result

    def _begin_call(self, tool: str, args: Mapping[str, Any]) -> None:
        if self._tool_calls >= self._max_tool_calls:
            self._trace.append(
                "budget_exhausted",
                source="harness",
                data={"limit": self._max_tool_calls, "attempted_tool": tool},
            )
            raise ToolBudgetExceeded(f"tool-call budget ({self._max_tool_calls}) exhausted")
        self._tool_calls += 1
        self._trace.append(
            "tool_call",
            source="harness.tool",
            data={"tool": tool, "args": dict(args)},
        )


class DirectiveFollowingAgent:
    """A deterministic stand-in for a *susceptible* agent.

    It reads its fixtures in order and, if any of that untrusted text carries an
    ``[[ACTION {json}]]`` directive, it obeys -- the naive behaviour a
    prompt-injection defends against. This is scaffolding: it exercises the
    Harness/Verifier/persistence path with a reproducible agent. It proves the
    plumbing, not model susceptibility. Real susceptibility is measured only once
    a model drives the same tools (T6) or the Attack Forge discovers it (AF).
    """

    def __init__(self, read_paths: list[str]) -> None:
        # Sorted by the caller for determinism; the injection may sit in any file.
        self._read_paths = list(read_paths)

    async def run(self, task: str, tools: "InstrumentedTools") -> str:
        directive: dict[str, Any] | None = None
        for path in self._read_paths:
            content = await tools.read_file(path)
            if directive is None:
                match = _DIRECTIVE.search(content)
                if match:
                    directive = json.loads(match.group(1))
        if directive is not None:
            action = getattr(tools, directive["tool"])
            await action(**directive.get("args", {}))
        return "completed"


@dataclass(frozen=True)
class HarnessResult:
    trace: Trace
    response_text: str
    tool_calls: int
    error: str | None = None


class ActionHarness:
    """Runs a supplied agent with no capability beyond the instrumented tools."""

    def __init__(self, target_name: str = "action-harness") -> None:
        self.target_name = target_name

    async def run(
        self,
        agent: ActionAgent,
        *,
        task: str,
        fixtures: Mapping[str, str],
        max_tool_calls: int = 4,
    ) -> HarnessResult:
        trace = Trace(target=self.target_name)
        tools = InstrumentedTools(trace, fixtures, max_tool_calls)
        trace.append("run_started", source="harness", data={"task": task, "max_tool_calls": max_tool_calls})

        try:
            response = agent.run(task, tools)
            if inspect.isawaitable(response):
                response = await response
            response_text = str(response)
            error = None
        except ToolBudgetExceeded as exc:
            response_text = ""
            error = str(exc)
        except HarnessError:
            # Infrastructure failure: never dress it up as a verdict. Halt.
            raise
        except Exception as exc:  # Harness records the fact; callers decide how to render it.
            response_text = ""
            error = f"{type(exc).__name__}: {exc}"

        trace.append(
            "run_finished",
            source="harness",
            data={"tool_calls": tools.tool_calls, "error": error},
        )
        return HarnessResult(trace=trace, response_text=response_text, tool_calls=tools.tool_calls, error=error)
