"""Adjudication: the deterministic Verifier, and nothing else.

The text-channel Judge and the judge-training flywheel are ASRT-private and are
not part of asrt-bench. A pack's verdict here is always a predicate over a
recorded Trace -- a fact, never a model's opinion.
"""

from .verifier import Verifier

__all__ = ["Verifier"]
