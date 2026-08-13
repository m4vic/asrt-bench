"""Declarative, serializable success criteria for action-channel attacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


CRITERIA_VERSION = "asrt.success-criteria/v1"


class CriteriaValidationError(ValueError):
    """Raised before a malformed criterion can be mistaken for a clean result."""


@dataclass(frozen=True)
class SuccessCriteria:
    """Validated wrapper around a JSON-compatible predicate expression."""

    channel: str
    expression: Mapping[str, Any]
    version: str = CRITERIA_VERSION

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SuccessCriteria":
        if not isinstance(raw, Mapping):
            raise CriteriaValidationError("success_criteria must be an object")
        channel = raw.get("channel")
        if channel != "action":
            raise CriteriaValidationError("Verifier accepts only success_criteria.channel='action'")
        version = raw.get("version", CRITERIA_VERSION)
        if version != CRITERIA_VERSION:
            raise CriteriaValidationError(f"unsupported success criteria version: {version!r}")
        expression = {key: value for key, value in raw.items() if key not in {"channel", "version"}}
        _validate_expression(expression)
        return cls(channel=channel, expression=expression, version=version)


def _validate_expression(expression: Mapping[str, Any]) -> None:
    if not isinstance(expression, Mapping) or not expression:
        raise CriteriaValidationError("success_criteria needs one predicate expression")

    logical = {key for key in ("all_of", "any_of", "none_of") if key in expression}
    if logical:
        if len(logical) != 1 or len(expression) != 1:
            raise CriteriaValidationError("a logical predicate must contain exactly one of all_of, any_of, none_of")
        children = expression[next(iter(logical))]
        if not isinstance(children, list) or not children:
            raise CriteriaValidationError("logical predicates need a non-empty list")
        for child in children:
            _validate_expression(child)
        return

    allowed = {"tool_called", "where"}
    unknown = set(expression) - allowed
    if unknown or "tool_called" not in expression:
        raise CriteriaValidationError("a leaf predicate requires tool_called and optional where")
    if not isinstance(expression["tool_called"], str) or not expression["tool_called"]:
        raise CriteriaValidationError("tool_called must be a non-empty string")
    where = expression.get("where", {})
    if not isinstance(where, Mapping):
        raise CriteriaValidationError("where must be an object")
    for path, condition in where.items():
        if not isinstance(path, str) or not path.startswith("args."):
            raise CriteriaValidationError("where paths must begin with 'args.'")
        if not isinstance(condition, Mapping) or len(condition) != 1:
            raise CriteriaValidationError("each where condition must contain exactly one operator")
        operator = next(iter(condition))
        if operator not in {"equals", "contains", "domain_equals"}:
            raise CriteriaValidationError(f"unsupported where operator: {operator!r}")
