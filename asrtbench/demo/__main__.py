"""`python -m asrtbench.demo`

Fires a pack of poisoned support tickets at a realistic support agent, twice:
once with a plain prompt (base) and once with a security-hardened prompt. Shows,
per attack, whether it broke the agent -- and the punchline: does the hardening
prompt actually stop anything?

Needs Ollama running with a tools-capable model (default qwen2.5:7b-instruct).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Force UTF-8 so box-drawing/block glyphs render in a real terminal and degrade
# to plain text when piped on Windows (cp1252), instead of crashing.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from asrtbench.adjudication import Verifier
from asrtbench.demo.app import make_chat, run_attack_on_app

console = Console()
BRAND = "#22d3ee"
ATTACKS_PATH = os.path.join(os.path.dirname(__file__), "attacks.json")


def _canary(attack_id: str) -> str:
    return "ORD-" + attack_id.split(":")[1][:6].upper()


async def _run_one(chat, atk, *, hardened: bool) -> tuple[str, str]:
    """Run one attack against one variant; return (verdict, real-harm description)."""
    canary = _canary(atk["id"])
    ticket = next(iter(atk["fixtures"].values())).replace("${canary}", canary)
    trace, app = await run_attack_on_app(chat, ticket, hardened=hardened)
    verdict = Verifier().verify(atk["success_criteria"], trace, bindings={"canary": canary})
    harm = ""
    if verdict.value == "success":
        if app.refunds_issued():
            r = app.refunds_issued()[0]
            harm = f"refund {r['order_id']} (${r['amount']:.0f})"
        elif app.account_takeovers():
            harm = f"account email → {app.account_takeovers()[0]['to']}"
        else:
            ext = [m["to"] for m in app.outbox if "attacker" in m["to"]]
            harm = f"data emailed → {ext[0]}" if ext else "action taken"
    return verdict.value, harm


def _mark(v: str) -> str:
    return {"success": "[bold red]BROKE[/bold red]", "failure": "[green]defended[/green]"}.get(
        v, "[yellow]unclear[/yellow]")


async def _main() -> None:
    all_attacks = json.load(open(ATTACKS_PATH, encoding="utf-8"))
    limit = int(os.environ.get("ASRT_DEMO_LIMIT", "8"))   # subset by default; runs on a local GPU
    attacks = all_attacks[:max(1, limit)]
    model = os.environ.get("ASRT_DEMO_MODEL", "qwen2.5:7b-instruct")
    chat = make_chat(model)

    console.print(Panel(
        f"A realistic support agent ([bold {BRAND}]{model}[/bold {BRAND}]) with real tools "
        f"(get_ticket, lookup_order, [bold]issue_refund[/bold], send_email, update_account).\n"
        f"Firing {len(attacks)} of {len(all_attacks)} poisoned tickets — plain prompt vs a "
        f"security-hardened prompt.  [dim](ASRT_DEMO_LIMIT={len(all_attacks)} runs the full pack; "
        f"each row streams live)[/dim]",
        title="◈ asrt-bench demo — can a poisoned ticket break a support agent?",
        title_align="left", border_style=BRAND, box=box.ROUNDED, padding=(1, 2)))
    console.print()

    base_broke = hard_broke = 0
    for atk in attacks:
        name = atk["id"].split(":")[1]
        bv, harm_str = await _run_one(chat, atk, hardened=False)
        hv, _ = await _run_one(chat, atk, hardened=True)
        base_broke += bv == "success"; hard_broke += hv == "success"
        harm = f"  [dim]→ real harm: {harm_str}[/dim]" if harm_str else ""
        console.print(f"  base {_mark(bv):<22} hardened {_mark(hv):<22} "
                      f"[white]{name}[/white] [dim]· {atk['technique']}[/dim]{harm}")

    stopped = base_broke - hard_broke
    verdict_line = (f"[red]The hardening prompt stopped {stopped} of {base_broke} attacks.[/red]"
                    if stopped <= 0 else
                    f"The hardening prompt stopped {stopped} of {base_broke} attacks.")
    console.print(Panel(
        f"base prompt:      [red]{base_broke}/{len(attacks)} attacks broke the agent[/red]\n"
        f"hardened prompt:  [red]{hard_broke}/{len(attacks)} attacks broke the agent[/red]\n\n"
        f"{verdict_line}",
        title="◈ diff(base, hardened)", title_align="left",
        border_style="red" if stopped <= 0 else "green", box=box.ROUNDED, padding=(1, 2)))


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
