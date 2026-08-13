"""Record/replay for model interactions (T7).

A model is stochastic; an unrecorded run cannot be reproduced, so a finding
cannot be re-examined and a regression cannot be told apart from sampling noise.
A cassette records every chat call keyed by (model id, exact messages) so replay
reconstructs the run with no network.

The one rule that keeps replay honest: a cassette miss in replay mode is a hard
error. Replay must never silently fall through to a live call -- that would make
a "reproduced" run secretly non-reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Mapping

from .action import HarnessError
from .model_agent import ChatFn


class CassetteMiss(HarnessError):
    """Replay asked for a response the cassette does not contain.

    A HarnessError, so it halts the run instead of being recorded as a verdict:
    a broken replay must never masquerade as an `unclear` result.
    """


class Cassette:
    """An ordered record of chat calls for one or more runs of a model."""

    def __init__(self, path: str, model_id: str) -> None:
        self.path = path
        self.model_id = model_id
        self.entries: list[dict[str, Any]] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.model_id = data.get("model_id", model_id)
            self.entries = data.get("entries", [])

    def _key(self, messages: list[dict[str, Any]]) -> str:
        payload = json.dumps({"model": self.model_id, "messages": messages}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def recording(self, inner: ChatFn) -> ChatFn:
        """Wrap a live chat_fn so every call is captured."""
        async def chat(messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]) -> Mapping[str, Any]:
            response = await inner(messages, tool_schemas)
            self.entries.append({"key": self._key(messages), "response": dict(response)})
            return response
        return chat

    def replaying(self) -> ChatFn:
        """A chat_fn that answers only from the cassette; a miss raises."""
        index = {entry["key"]: entry["response"] for entry in self.entries}

        async def chat(messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]) -> Mapping[str, Any]:
            key = self._key(messages)
            if key not in index:
                raise CassetteMiss(
                    f"no recorded response for messages hash {key[:12]} "
                    f"(model {self.model_id}); replay will not make a live call"
                )
            return index[key]
        return chat

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"model_id": self.model_id, "entries": self.entries}, handle, indent=2)
