"""The harness: run an agent against inert, instrumented tools and record a Trace.

asrt-bench carries only the generation-free half of ASRT's harness -- it fires a
FROZEN pack, it never discovers or mutates attacks. So `agent.py` (RAG delivery)
and `multiturn.py` (discovery escalation) are deliberately absent; their absence
is what keeps this repo unable to generate attacks, by construction.
"""

from .action import (
    ActionHarness, ActionAgent, DirectiveFollowingAgent, HarnessResult,
    HarnessError, InstrumentedTools, ToolBudgetExceeded,
)
from .model_agent import (
    ModelAgent, ollama_chat_fn, openai_chat_fn, TOOL_SCHEMAS, drive_tool_loop,
    ToolCallingUnsupported,
)

__all__ = [
    "ActionHarness", "ActionAgent", "DirectiveFollowingAgent", "HarnessResult",
    "HarnessError", "InstrumentedTools", "ToolBudgetExceeded",
    "ModelAgent", "ollama_chat_fn", "openai_chat_fn", "TOOL_SCHEMAS", "drive_tool_loop",
    "ToolCallingUnsupported",
]
