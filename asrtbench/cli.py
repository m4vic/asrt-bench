"""asrt-bench interactive console.

Two things, made simple and legible:

    /target <name>       what gets tested
    /run name=v1         fire the pack at it, save the result as version v1
    /diff v1 v2          what changed between two saved versions

A colored console so a first run is obvious, not a manual. Everything here is a
thin wrapper over the library (target.py, runner.py, store.py, diff.py) -- the
CLI decides nothing about a verdict.
"""

from __future__ import annotations

import shlex

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from asrtbench.target import Target
from asrtbench import runner, store
from asrtbench.diff import compare, IncomparableRuns

console = Console()

_LOGO = r"""
 ___  ___ ___ _____   ___  ___ _ _  ___ _  _
/   \/ __| _ \_   _| | _ )/ __| \ | / __| || |
| - |\__ \   / | |   | _ \ _| | .` | (__| __ |
|_|_|/___/_|_\ |_|   |___/___|_|\_|\___|_||_|
"""

VERDICT_STYLE = {
    "success": ("LANDED", "bold red"),
    "failure": ("defended", "green"),
    "unclear": ("unclear", "yellow"),
}


class Session:
    def __init__(self) -> None:
        self.target: Target = Target.fixture()


# ---------------------------------------------------------------- commands

def cmd_target(session: Session, args: dict) -> None:
    """/target <name|path>   choose the system under test (see /target list)."""
    ref = args.get("_pos")
    if ref == "list":
        names = Target.list_available()
        console.print("[dim]bundled targets:[/dim] " + ", ".join(f"[cyan]{n}[/cyan]" for n in names))
        console.print("[dim]or pass a path to your own config file[/dim]")
        return
    if ref:
        try:
            session.target = Target.resolve(ref)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            return
    _render_target(session.target)


def _render_target(target: Target) -> None:
    d = target.describe()
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim", justify="right", width=13)
    t.add_column()
    t.add_row("target", f"[cyan]{d['name']}[/cyan]")
    if d["kind"] == "model":
        t.add_row("model", f"{d['model']}  [dim]({d['provider']} @ {d['endpoint']})[/dim]")
    else:
        t.add_row("kind", "fixture  [dim](deterministic, no model)[/dim]")
    t.add_row("tools", ", ".join(d["tools"]))
    t.add_row("blast ceiling", str(d["blast_ceiling"]))
    console.print(Panel(t, border_style="cyan", expand=False))
    if d["kind"] == "model":
        missing = target.missing_credential()
        if missing:
            console.print(f"[yellow]needs {missing} in the environment before /run[/yellow]")


def cmd_run(session: Session, args: dict) -> None:
    """/run name=v1 [pack=<dir>]   fire the pack at the target, save as a version."""
    name = args.get("name") or args.get("_pos")
    if not name:
        console.print("[red]usage: /run name=v1   (a version name to save under)[/red]")
        return
    pack = args.get("pack")  # default: bundled starter pack

    if session.target.kind == "model":
        missing = session.target.missing_credential()
        if missing:
            console.print(f"[red]{session.target.name} needs {missing}. Set it, then retry.[/red]")
            return

    if store.exists(name):
        old = store.meta(name)
        console.print(f"[yellow]overwriting version '{name}'[/yellow] "
                      f"[dim](was {old['run_id']}, target {old['target']})[/dim]")

    console.print(Panel(f"firing pack at [cyan]{session.target.name}[/cyan] "
                        f"[dim](tools inert -- no real side effects)[/dim]",
                        border_style="magenta", expand=False))

    rows: list[Text] = []

    def emit(stage: str, p: dict) -> None:
        if stage == "case_verdict":
            label, style = VERDICT_STYLE.get(p["verdict"], (p["verdict"], "white"))
            br = (p.get("blast_radius") or {}).get("tool") or "-"
            line = Text()
            line.append(f"  {label:<9}", style=style)
            line.append(f"{p['attack_id']:<42}", style="dim")
            line.append(f"reached {br}", style="dim")
            console.print(line)

    try:
        result = runner.run_pack(session.target, pack, emit=emit)
    except Exception as exc:
        console.print(f"[red]run failed: {type(exc).__name__}: {exc}[/red]")
        return

    path = store.save(result, name)
    c = result.counts()
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim", justify="right", width=9)
    summary.add_column()
    summary.add_row("landed", f"[bold red]{c['success']}[/bold red]")
    summary.add_row("defended", f"[green]{c['failure']}[/green]")
    summary.add_row("unclear", f"[yellow]{c['unclear']}[/yellow]  [dim](never pass or fail)[/dim]")
    summary.add_row("saved", f"version [cyan]{name}[/cyan]  [dim]{path}[/dim]")
    console.print(Panel(summary, title="run complete", border_style="cyan", expand=False))


def cmd_diff(session: Session, args: dict) -> None:
    """/diff <baseline> <candidate>   what changed between two saved versions."""
    a = args.get("_pos")
    b = args.get("_pos2")
    versions = store.list_versions()

    if not a or not b:
        if len(versions) >= 2:
            console.print("[dim]saved versions (newest first):[/dim]")
            for v in versions:
                console.print(f"  [cyan]{v['version']}[/cyan]  [dim]{v['target']}  "
                              f"landed={v['counts']['success']}[/dim]")
            console.print("\n[dim]compare two:[/dim]  /diff <baseline> <candidate>")
            # Offer the obvious adjacent pair.
            newer, older = versions[0]["version"], versions[1]["version"]
            console.print(f"[dim]e.g.[/dim]  /diff {older} {newer}")
        else:
            console.print("[yellow]need at least two saved versions to diff. "
                          "Run /run name=v1 then /run name=v2 first.[/yellow]")
        return

    try:
        baseline = store.load(a)
        candidate = store.load(b)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    try:
        report = compare(baseline, candidate, baseline_name=a, candidate_name=b)
    except IncomparableRuns as exc:
        console.print(Panel(f"[red]cannot compare:[/red] {exc}", border_style="red", expand=False))
        return

    _render_diff(report)


def _render_diff(report) -> None:
    head = {"regressed": ("REGRESSED", "bold red"),
            "improved": ("IMPROVED", "green"),
            "unchanged": ("UNCHANGED", "cyan")}[report.verdict()]
    console.print(Panel(
        f"[{head[1]}]{head[0]}[/{head[1]}]   [dim]{report.baseline} -> {report.candidate}[/dim]",
        border_style=head[1].split()[-1], expand=False))

    if report.newly_broken:
        console.print("[bold red]newly broken[/bold red]  [dim](safe before, exploitable now)[/dim]")
        for ch in report.newly_broken:
            console.print(f"  [red]x[/red] {ch.attack_id}  [dim]{ch.baseline} -> {ch.candidate}[/dim]")
    if report.newly_fixed:
        console.print("[green]newly fixed[/green]  [dim](exploitable before, safe now)[/dim]")
        for ch in report.newly_fixed:
            console.print(f"  [green]+[/green] {ch.attack_id}  [dim]{ch.baseline} -> {ch.candidate}[/dim]")
    if report.inconclusive:
        console.print("[yellow]inconclusive[/yellow]  [dim](a move involving unclear -- not counted)[/dim]")
        for ch in report.inconclusive:
            console.print(f"  [yellow]?[/yellow] {ch.attack_id}  [dim]{ch.baseline} -> {ch.candidate}[/dim]")

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim", justify="right", width=14)
    t.add_column()
    t.add_row("stable broken", str(len(report.stable_broken)))
    t.add_row("stable safe", str(len(report.stable_safe)))
    if report.only_in_baseline or report.only_in_candidate:
        t.add_row("not in both", f"{len(report.only_in_baseline)} only baseline, "
                                  f"{len(report.only_in_candidate)} only candidate")
    console.print(t)


def cmd_versions(session: Session, args: dict) -> None:
    """/versions   list saved runs."""
    versions = store.list_versions()
    if not versions:
        console.print("[dim]no saved versions yet. /run name=v1 to make one.[/dim]")
        return
    t = Table(header_style="bold cyan", expand=False)
    t.add_column("version")
    t.add_column("target", style="dim")
    t.add_column("landed", justify="right")
    t.add_column("pack", style="dim")
    for v in versions:
        t.add_row(v["version"], v["target"], str(v["counts"]["success"]), v["pack_hash"][:12])
    console.print(t)


def cmd_status(session: Session, args: dict) -> None:
    """/status   the current target and saved versions."""
    _render_target(session.target)
    cmd_versions(session, {})


HELP = """
[bold cyan]asrt-bench[/bold cyan] [dim]— fire a pack, verify what lands, diff versions[/dim]

  [bold]/target[/bold] [dim]<name>|list[/dim]        choose the system under test
  [bold]/run[/bold] [dim]name=v1[/dim]               fire the pack at it, save as version v1
  [bold]/diff[/bold] [dim]<v1> <v2>[/dim]            what changed between two versions
  [bold]/versions[/bold]                 list saved runs
  [bold]/status[/bold]                   current target + versions
  [bold]/help[/bold]  [bold]/quit[/bold]

[dim]tools are inert: nothing is emailed, written, queried, or executed for real.[/dim]
"""

COMMANDS = {
    "target": cmd_target, "run": cmd_run, "diff": cmd_diff,
    "versions": cmd_versions, "status": cmd_status,
}


def parse(line: str) -> tuple[str, dict]:
    parts = shlex.split(line)
    cmd = parts[0].lstrip("﻿").lstrip("/")
    args: dict = {}
    positional = 0
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            args[k] = v
        else:
            positional += 1
            args["_pos" if positional == 1 else f"_pos{positional}"] = p
    return cmd, args


def main() -> None:
    console.print(Text(_LOGO, style="bold cyan"))
    console.print("[dim]Automated Safety Regression Testing — bench[/dim]  "
                  "[dim]·  /help to begin[/dim]\n")
    session = Session()

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.completion import WordCompleter
        completer = WordCompleter(["/" + c for c in COMMANDS] + ["/help", "/quit"],
                                  ignore_case=True, sentence=True)
        pt = PromptSession(history=InMemoryHistory(), completer=completer)
        read = lambda: pt.prompt("asrt-bench ❯ ")
    except Exception:
        read = lambda: input("asrt-bench > ")

    while True:
        try:
            line = read().strip().lstrip("﻿")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return
        if not line:
            continue
        if line in ("/quit", "/exit", "quit", "exit"):
            console.print("[dim]bye[/dim]")
            return
        if line in ("/help", "help", "?"):
            console.print(HELP)
            continue

        cmd, args = parse(line)
        fn = COMMANDS.get(cmd)
        if not fn:
            console.print(f"[red]unknown command '{cmd}'[/red]  try /help")
            continue
        try:
            fn(session, args)
        except Exception as exc:
            console.print_exception(max_frames=6)
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]\n")


if __name__ == "__main__":
    main()
