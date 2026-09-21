"""A `.py` target for an agent that routes its own tool calls.

`attach()` fits agents where the MODEL picks the tool and asrt-bench drives the
loop. This file shows the other common shape: a router in your own code decides
which tool to call and calls it directly. LangGraph nodes, hand-written
dispatchers and most "agent frameworks with a graph" work this way.

Run it:

    python -m asrtbench
    asrt-bench > /target ./examples/code_routed_agent.py
    asrt-bench > /run name=v1 pack=./examples/code_routed_pack

Use that pack, not the bundled starter one: a pack's criteria name specific
tools, and the starter pack names the harness's inert tools (`send_email`,
`read_file`), which this agent does not have.

Expect three verdicts -- the attack LANDED, the benign control defended, and the
wiring control LANDED. That last one is not a vulnerability; it proves the
recorder is actually on the wire. If it comes back defended, nothing was
observed and the other two verdicts mean nothing.

No model or network needed -- the routing below is plain Python, so the example
stays deterministic and the point stays visible.

The three names asrt-bench looks for are NAME, TOOLS and run(), at the bottom.
"""

from __future__ import annotations


# --- YOUR app: real tools, called directly by your own router ----------------

def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "delivered", "amount": 23}


def update_account(account_id: str, change: str) -> dict:
    """The privileged one. In a real system this changes a login or an email."""
    return {"account_id": account_id, "change": change, "status": "pending_approval"}


def handle_ticket(ticket: str) -> dict:
    """Route a support ticket to a tool.

    The `else` branch is the interesting part, and it is not a contrived bug --
    it is what most routers do. Anything the classifier does not recognise falls
    through to the most privileged branch, so text crafted to match *nothing*
    reaches `update_account`. Fail-open routing is the vulnerability this target
    exists to demonstrate.
    """
    text = ticket.lower()
    if "refund" in text or "order" in text:
        return lookup_order("12312")
    if "policy" in text or "how do i" in text:
        return {"answer": "refunds are accepted within 30 days"}
    return update_account("acct-1", "email")


# --- The target declaration asrt-bench reads ---------------------------------

NAME = "code-routed-support-agent"

# (module, "function_name") for every tool to record.
#
# Patch where a tool is CALLED, not where it is defined. Here both live in this
# module, so `sys.modules[__name__]` is the right owner. If your router lived in
# `flow.py` and did `from tools import lookup_order`, you would name
# `(flow, "lookup_order")` -- patching `tools` would record nothing, and an empty
# trace reads as `defended`, which is a false clean pass rather than an error.
import sys

_this_module = sys.modules[__name__]

TOOLS = [
    (_this_module, "lookup_order"),
    (_this_module, "update_account"),
]


def run(payload: str) -> None:
    """Feed one poisoned payload through your agent's normal front door.

    asrt-bench calls this once per attack, with the attack's poisoned text and
    its canary already substituted in. Nothing else about your app changes.
    """
    handle_ticket(payload)
