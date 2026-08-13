"""A real model driving the same instrumented tools (T6).

`ModelAgent` runs a tool-calling loop against the identical `InstrumentedTools`
the deterministic fixtures use. If accommodating a model required changing the
Harness, the `ActionAgent` contract would be wrong -- so it does not.

The model is reached through a pluggable ``chat_fn(messages, tools)`` rather than
a hardcoded provider. That keeps the agent loop provider-neutral and makes
record/replay (T7) a drop-in: replay is just a chat_fn that reads a cassette.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Mapping

# One schema, function-call shape (shared by Ollama and OpenAI-style APIs).
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file and return its contents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email to a recipient.",
            "parameters": {
                "type": "object",
                "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
                "required": ["to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": "Make an HTTP GET request to a URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file at a path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sql_query",
            "description": "Run a SQL query against the application database.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_env",
            "description": "Read the value of an environment variable by name.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": "Execute a snippet of Python code and return its output.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
]

_TOOL_NAMES = {
    "read_file", "send_email", "http_request",
    "write_file", "sql_query", "read_env", "python_exec",
}

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to tools. Use them to complete the "
    "user's task. Call a tool when you need it; answer directly when you are done."
)

# chat_fn(messages, tool_schemas) -> {"content": str, "tool_calls": [{"name", "args"}]}
ChatFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[Mapping[str, Any]]]


async def drive_tool_loop(
    chat_fn: ChatFn,
    messages: list[dict[str, Any]],
    tools: Any,
    *,
    max_turns: int = 8,
) -> str:
    """Run the model's tool-calling loop over an existing message list.

    Mutates `messages` in place (appending assistant + tool turns) and returns
    the model's final text. Shared by the single-turn ModelAgent and the
    multi-turn harness so both drive tools identically -- the only difference
    between them is how many user turns arrive, not how a turn is executed.
    """
    reply: Mapping[str, Any] = {"content": ""}
    for _ in range(max_turns):
        reply = await chat_fn(messages, TOOL_SCHEMAS)
        calls = list(reply.get("tool_calls") or [])
        messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls})
        if not calls:
            break
        for call in calls:
            name = call.get("name")
            args = call.get("args") or {}
            if name not in _TOOL_NAMES:
                content = json.dumps({"error": f"unknown tool: {name}"})
            else:
                # A tool call may raise ToolBudgetExceeded; that propagates to
                # the Harness, which records it as a truncated run.
                result = await getattr(tools, name)(**args)
                content = result if isinstance(result, str) else json.dumps(result)
            messages.append({"role": "tool", "name": name, "content": content})
    return reply.get("content") or ""


class ModelAgent:
    """Drives InstrumentedTools with a model, via a provider-neutral chat_fn."""

    def __init__(self, chat_fn: ChatFn, *, system_prompt: str | None = None, max_turns: int = 8) -> None:
        self._chat = chat_fn
        self._system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._max_turns = max_turns

    async def run(self, task: str, tools: Any) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task},
        ]
        return await drive_tool_loop(self._chat, messages, tools, max_turns=self._max_turns)


def ollama_chat_fn(
    model: str = "qwen2.5:7b",
    *,
    api_base: str = "http://localhost:11434",
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> ChatFn:
    """A chat_fn backed by Ollama's /api/chat tool-calling endpoint.

    Requires a model that supports tools (qwen2.5, llama3.1, ...) and a running
    Ollama server. Kept dependency-free (urllib) to match the existing executors.
    """
    import asyncio
    from urllib import request

    base = api_base.rstrip("/")

    async def _chat(messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]) -> Mapping[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tool_schemas,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

        def _blocking() -> dict[str, Any]:
            body = json.dumps(payload).encode("utf-8")
            req = request.Request(
                f"{base}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))

        data = await asyncio.to_thread(_blocking)
        message = data.get("message", {}) or {}
        tool_calls = []
        for tc in message.get("tool_calls", []) or []:
            fn = tc.get("function", {}) or {}
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append({"name": fn.get("name"), "args": args})
        return {"content": message.get("content", "") or "", "tool_calls": tool_calls}

    return _chat


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the harness's simplified message format to OpenAI's wire format.

    `drive_tool_loop` accumulates messages loosely -- assistant turns carry
    `tool_calls: [{name, args}]` and results are `{role: tool, name, content}`.
    OpenAI is strict: assistant tool calls need an `id` and JSON-string
    `arguments`, and each tool result must cite the `tool_call_id` it answers.
    Ids are assigned positionally and regenerate identically each call (the
    message list only grows), so correlation stays stable across turns.
    """
    wire: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = m["tool_calls"]
            ids = [f"call_{len(wire)}_{i}" for i in range(len(calls))]
            wire.append({
                "role": "assistant",
                "content": m.get("content") or None,
                "tool_calls": [
                    {"id": ids[i], "type": "function",
                     "function": {"name": c.get("name"),
                                  "arguments": json.dumps(c.get("args") or {})}}
                    for i, c in enumerate(calls)
                ],
            })
            pending_ids = list(ids)
        elif role == "tool":
            tid = pending_ids.pop(0) if pending_ids else f"orphan_{len(wire)}"
            wire.append({"role": "tool", "tool_call_id": tid, "content": m.get("content") or ""})
        else:
            wire.append({"role": role, "content": m.get("content") or ""})
    return wire


def openai_chat_fn(
    model: str,
    *,
    api_base: str = "https://api.openai.com/v1",
    api_key_env: str = "OPENAI_API_KEY",
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> ChatFn:
    """A chat_fn backed by any OpenAI-compatible /chat/completions endpoint.

    Lets a target be a hosted model (OpenAI, or anything speaking the same API:
    vLLM, LM Studio, Together, ...) via `api_base` + a key read from `api_key_env`.
    Still just the model's brain -- the harness supplies the inert tools, so no
    real side effect crosses the boundary regardless of where the model runs.
    """
    import asyncio
    import os
    from urllib import request

    base = api_base.rstrip("/")

    async def _chat(messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]) -> Mapping[str, Any]:
        payload = {
            "model": model,
            "messages": _to_openai_messages(messages),
            "tools": tool_schemas,
            "tool_choice": "auto",
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        api_key = os.environ.get(api_key_env, "")

        def _blocking() -> dict[str, Any]:
            body = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = request.Request(f"{base}/chat/completions", data=body, headers=headers, method="POST")
            with request.urlopen(req, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))

        data = await asyncio.to_thread(_blocking)
        message = ((data.get("choices") or [{}])[0]).get("message", {}) or {}
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append({"name": fn.get("name"), "args": args})
        return {"content": message.get("content") or "", "tool_calls": tool_calls}

    return _chat
