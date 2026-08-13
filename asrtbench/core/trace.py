"""Immutable-facing, append-only evidence contracts for the action channel."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


def _freeze(value: Any) -> Any:
    """Recursively detach and freeze JSON-shaped event data."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return deepcopy(value)


def _thaw(value: Any) -> Any:
    """Convert an immutable event value back to ordinary JSON-shaped data."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return deepcopy(value)


def _frozen_mapping(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Copy event data before exposing it so later caller mutations cannot rewrite it."""
    return _freeze(dict(data or {}))


@dataclass(frozen=True)
class TraceEvent:
    """One observed Harness event with a sequence assigned by :class:`Trace`."""

    seq: int
    kind: str
    source: str
    at: datetime
    data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "source": self.source,
            "at": self.at.isoformat(),
            "data": _thaw(self.data),
        }


@dataclass
class Trace:
    """Ordered run evidence.

    Callers append facts through ``append``. They receive an immutable tuple of
    immutable-facing events, so a Verifier can trust the sequence it receives.
    """

    target: str = "action-harness"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _events: list[TraceEvent] = field(default_factory=list, init=False, repr=False)

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def completeness(self) -> tuple[bool, str | None, int | None]:
        """Derive from the trace's own events whether the run finished cleanly.

        A run is incomplete when it carries an explicit truncation signal: a
        ``budget_exhausted`` event, or a ``run_finished`` reporting an error.
        The Verifier uses this so that *absence* of evidence on a run we stopped
        watching is never scored as a clean result.

        Returns ``(is_complete, reason, terminating_seq)`` where ``reason`` and
        ``terminating_seq`` are ``None`` for a complete run.
        """
        for event in self._events:
            if event.kind == "budget_exhausted":
                return False, "run truncated: tool-call budget exhausted", event.seq
            if event.kind == "run_finished" and event.data.get("error") is not None:
                return False, f"run truncated: {event.data['error']}", event.seq
        return True, None, None

    @property
    def is_complete(self) -> bool:
        return self.completeness()[0]

    def append(self, kind: str, *, source: str, data: Mapping[str, Any] | None = None) -> TraceEvent:
        if not kind:
            raise ValueError("Trace event kind is required")
        if not source:
            raise ValueError("Trace event source is required")
        event = TraceEvent(
            seq=len(self._events) + 1,
            kind=kind,
            source=source,
            at=datetime.now(timezone.utc),
            data=_frozen_mapping(data),
        )
        self._events.append(event)
        return event

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "started_at": self.started_at.isoformat(),
            "events": [event.to_dict() for event in self._events],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Trace":
        """Rehydrate a Trace from :meth:`to_dict` output.

        Sequence numbers are restored exactly as stored, so a persisted run
        re-verifies to the same Verdict it produced when it ran.
        """
        trace = cls(target=payload.get("target", "action-harness"))
        started = payload.get("started_at")
        if started:
            trace.started_at = datetime.fromisoformat(started)
        for event in payload.get("events", []):
            trace._events.append(
                TraceEvent(
                    seq=event["seq"],
                    kind=event["kind"],
                    source=event["source"],
                    at=datetime.fromisoformat(event["at"]),
                    data=_frozen_mapping(event.get("data")),
                )
            )
        return trace
