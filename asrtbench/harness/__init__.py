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
from .targets import TargetExecutor, MockExecutor, OllamaExecutor, OpenAIExecutor, LiteLLMExecutor
from .model_agent import (
    ModelAgent, ollama_chat_fn, openai_chat_fn, TOOL_SCHEMAS, drive_tool_loop,
    ToolCallingUnsupported,
)
from .cassette import Cassette, CassetteMiss

__all__ = [
    "ActionHarness", "ActionAgent", "DirectiveFollowingAgent", "HarnessResult",
    "HarnessError", "InstrumentedTools", "ToolBudgetExceeded",
    "TargetExecutor", "MockExecutor", "OllamaExecutor", "OpenAIExecutor", "LiteLLMExecutor",
    "ModelAgent", "ollama_chat_fn", "openai_chat_fn", "TOOL_SCHEMAS", "drive_tool_loop",
    "ToolCallingUnsupported",
    "Cassette", "CassetteMiss",
]
