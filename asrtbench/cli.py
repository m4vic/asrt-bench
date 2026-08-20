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
import sys

# The console uses box-drawing and block glyphs. On Windows a piped or legacy
# console defaults to cp1252 and cannot encode them -- force UTF-8 so the UI
# renders in a real terminal and degrades to plain UTF-8 text when piped,
# instead of crashing.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from asrtbench.target import Target
from asrtbench import runner, store, attack_api
from asrtbench.diff import compare, IncomparableRuns

console = Console()

# One brand colour and a small, consistent palette used everywhere:
#   cyan    structure / brand           red     an attack LANDED (bad)
#   green   defended (good)             yellow  unclear (neither)
#   magenta the active run
BRAND = "#22d3ee"   # cyan

_LOGO = r"""
 ▄▀█ █▀ █▀█ ▀█▀   █▄▄ █▀▀ █▄░█ █▀▀ █░█
 █▀█ ▄█ █▀▄ ░█░   █▄█ ██▄ █░▀█ █▄▄ █▀█
"""

# label, icon, style — the single source of truth for how a verdict looks.
VERDICT = {
    "success": ("LANDED",   "✗", "bold red"),
    "failure": ("defended", "✓", "green"),
    "unclear": ("unclear",  "•", "yellow"),
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
        chips = "  ".join(f"[{BRAND}]{n}[/{BRAND}]" for n in names)
        console.print(Panel(
            Group(Text("bundled targets", style="dim"), Text(""), Text.from_markup("  " + chips),
                  Text(""), Text("…or pass a path to your own JSON config", style="dim italic")),
            title="targets", title_align="left", border_style=BRAND, box=box.ROUNDED, padding=(0, 2)))
        return
    if ref:
        try:
            session.target = Target.resolve(ref)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"  [red]✗ {exc}[/red]")
            return
    _render_target(session.target)


def _render_target(target: Target) -> None:
    d = target.describe()
    t = Table.grid(padding=(0, 3))
    t.add_column(style="dim", justify="right", width=9)
    t.add_column()
    t.add_row("target", f"[bold {BRAND}]{d['name']}[/bold {BRAND}]")
    if d["kind"] == "model":
        t.add_row("model", f"{d['model']}")
        t.add_row("via", f"[dim]{d['provider']} · {d['endpoint']}[/dim]")
    else:
        t.add_row("kind", "fixture  [dim]· deterministic, no model[/dim]")
    t.add_row("tools", "[dim]" + ", ".join(d["tools"]) + "[/dim]")
    t.add_row("max blast", _blast_bar(d["blast_ceiling"]))
    console.print(Panel(t, title="◈ target", title_align="left",
                        border_style=BRAND, box=box.ROUNDED, padding=(1, 2), expand=False))
    if d["kind"] == "model":
        missing = target.missing_credential()
        if missing:
            console.print(f"  [yellow]⚠ needs {missing} in the environment before /run[/yellow]")


def _blast_bar(ceiling: int) -> str:
    """A tiny 1-10 severity meter, so the target's reach is visible at a glance."""
    filled = "█" * ceiling
    empty = "░" * (10 - ceiling)
    colour = "red" if ceiling >= 8 else ("yellow" if ceiling >= 4 else "green")
    return f"[{colour}]{filled}[/{colour}][dim]{empty}[/dim]  [dim]{ceiling}/10[/dim]"


def cmd_run(session: Session, args: dict) -> None:
    """/run name=v1 [pack=<dir>]   fire the pack at the target, save as a version."""
    name = args.get("name") or args.get("_pos")
    if not name:
        console.print("[red]usage: /run name=v1   (a version name to save under)[/red]")
        return
    pack = args.get("pack")  # default: bundled starter pack

    # source=api fetches a fresh pack from the ASRT attack API instead of the
    # bundled one. An attack is just data, so a fetched pack runs identically.
    source = args.get("source", "prebuilt")
    if source == "api":
        cfg = attack_api.ApiConfig.from_env()
        if not cfg.is_configured:
            _render_api_status(cfg)
            return
        try:
            with console.status("[cyan]fetching a fresh pack from the attack API…[/cyan]"):
                pack = attack_api.fetch_pack(
                    cfg,
                    target_profile=session.target.describe(),
                    count=int(args.get("count", 12)),
                    mode=args.get("mode", "discovery"),
                )
            console.print(f"  [{BRAND}]✓ pulled a fresh pack from the attack API[/{BRAND}]  [dim]· source=api[/dim]")
        except attack_api.AttackAPIError as exc:
            console.print(f"  [red]✗ attack API: {exc}[/red]")
            return
    elif source != "prebuilt":
        console.print(f"  [red]✗ unknown source '{source}' (use prebuilt | api)[/red]")
        return

    if session.target.kind == "model":
        missing = session.target.missing_credential()
        if missing:
            console.print(f"[red]{session.target.name} needs {missing}. Set it, then retry.[/red]")
            return
        with console.status("[cyan]checking the target model supports tool-calling…[/cyan]"):
            tool_err = session.target.tool_support_error()
        if tool_err:
            console.print(Panel(tool_err, title="◈ target can't be used", title_align="left",
                                border_style="red", box=box.ROUNDED, padding=(1, 2), expand=False))
            return

    if store.exists(name):
        old = store.meta(name)
        console.print(f"  [yellow]⚠ overwriting version '{name}'[/yellow] "
                      f"[dim]· was {old['run_id']}, target {old['target']}[/dim]")

    console.print()
    console.print(Rule(f"[magenta]▶ firing pack at[/magenta] [bold {BRAND}]{session.target.name}[/bold {BRAND}]"
                       f"   [dim]tools inert — no real side effects[/dim]",
                       style="magenta", align="left"))

    def emit(stage: str, p: dict) -> None:
        if stage == "case_verdict":
            label, icon, style = VERDICT.get(p["verdict"], (p["verdict"], "?", "white"))
            br = (p.get("blast_radius") or {}).get("tool") or "—"
            line = Text("  ")
            line.append(f"{icon} ", style=style)
            line.append(f"{label:<9}", style=style)
            line.append(f"{p['attack_id']:<40}", style="white")
            line.append(f"→ {br}", style="dim")
            console.print(line)

    try:
        result = runner.run_pack(session.target, pack, emit=emit)
    except Exception as exc:
        console.print(f"  [red]✗ run failed: {type(exc).__name__}: {exc}[/red]")
        return

    path = store.save(result, name)
    c = result.counts()
    total = c["total"] or 1
    bar = _stack_bar(c["success"], c["failure"], c["unclear"], total)

    summary = Table.grid(padding=(0, 3))
    summary.add_column(style="dim", justify="right", width=9)
    summary.add_column()
    summary.add_row("", bar)
    summary.add_row("landed", f"[bold red]{c['success']}[/bold red]  [dim]attacks got through[/dim]")
    summary.add_row("defended", f"[green]{c['failure']}[/green]")
    summary.add_row("unclear", f"[yellow]{c['unclear']}[/yellow]  [dim]· never pass or fail[/dim]")
    summary.add_row("", "")
    summary.add_row("saved as", f"[bold {BRAND}]{name}[/bold {BRAND}]  [dim]· {path}[/dim]")
    console.print(Panel(summary, title="✓ run complete", title_align="left",
                        border_style="green" if not c["success"] else "red",
                        box=box.ROUNDED, padding=(1, 2), expand=False))


def _stack_bar(landed: int, defended: int, unclear: int, total: int, width: int = 30) -> Text:
    """A single proportional bar: red landed | green defended | yellow unclear."""
    def seg(n, ch, style):
        w = round(width * n / total)
        return Text(ch * w, style=style) if w else Text("")
    bar = Text()
    bar.append_text(seg(landed, "█", "red"))
    bar.append_text(seg(defended, "█", "green"))
    bar.append_text(seg(unclear, "█", "yellow"))
    bar.append(f"  {landed}/{total} landed", style="dim")
    return bar


def cmd_diff(session: Session, args: dict) -> None:
    """/diff <baseline> <candidate>   what changed between two saved versions."""
    a = args.get("_pos")
    b = args.get("_pos2")
    versions = store.list_versions()

    if not a or not b:
        if len(versions) >= 2:
            newer, older = versions[0]["version"], versions[1]["version"]
            body = [Text("saved versions — pick two to compare", style="dim"), Text("")]
            for v in versions:
                body.append(Text.from_markup(
                    f"  [bold {BRAND}]{v['version']:<12}[/bold {BRAND}] "
                    f"[dim]{v['target']:<20} {v['counts']['success']} landed[/dim]"))
            body += [Text(""), Text.from_markup(f"  [dim]try:[/dim]  /diff {older} {newer}")]
            console.print(Panel(Group(*body), title="◈ diff", title_align="left",
                                border_style=BRAND, box=box.ROUNDED, padding=(1, 2)))
        else:
            console.print("  [yellow]⚠ need two saved versions. Run /run name=v1, then /run name=v2.[/yellow]")
        return

    try:
        baseline = store.load(a)
        candidate = store.load(b)
    except FileNotFoundError as exc:
        console.print(f"  [red]✗ {exc}[/red]")
        return

    try:
        report = compare(baseline, candidate, baseline_name=a, candidate_name=b)
    except IncomparableRuns as exc:
        console.print(Panel(f"[red]✗ cannot compare[/red]\n\n{exc}", title="diff refused",
                            title_align="left", border_style="red", box=box.ROUNDED, padding=(1, 2)))
        return

    _render_diff(report)


def _render_diff(report) -> None:
    head = {"regressed": ("REGRESSED", "bold white on red", "red"),
            "improved":  ("IMPROVED", "bold white on green", "green"),
            "unchanged": ("UNCHANGED", f"bold {BRAND}", BRAND)}[report.verdict()]

    body = [Text.from_markup(
        f" [{head[1]}] {head[0]} [/{head[1]}]   "
        f"[dim]{report.baseline}  →  {report.candidate}[/dim]"), Text("")]

    if report.newly_broken:
        body.append(Text.from_markup("[bold red]▼ newly broken[/bold red]  [dim]safe before, exploitable now[/dim]"))
        for ch in report.newly_broken:
            body.append(Text.from_markup(f"    [red]✗[/red] {ch.attack_id}  [dim]{ch.baseline} → {ch.candidate}[/dim]"))
        body.append(Text(""))
    if report.newly_fixed:
        body.append(Text.from_markup("[bold green]▲ newly fixed[/bold green]  [dim]exploitable before, safe now[/dim]"))
        for ch in report.newly_fixed:
            body.append(Text.from_markup(f"    [green]✓[/green] {ch.attack_id}  [dim]{ch.baseline} → {ch.candidate}[/dim]"))
        body.append(Text(""))
    if report.inconclusive:
        body.append(Text.from_markup("[yellow]• inconclusive[/yellow]  [dim]a move involving unclear — not counted[/dim]"))
        for ch in report.inconclusive:
            body.append(Text.from_markup(f"    [yellow]•[/yellow] {ch.attack_id}  [dim]{ch.baseline} → {ch.candidate}[/dim]"))
        body.append(Text(""))

    body.append(Text.from_markup(
        f"[dim]stable:[/dim] [red]{len(report.stable_broken)} broken[/red] · "
        f"[green]{len(report.stable_safe)} safe[/green]"
        + (f"   [dim]· {len(report.only_in_baseline)}+{len(report.only_in_candidate)} not in both[/dim]"
           if report.only_in_baseline or report.only_in_candidate else "")))

    console.print(Panel(Group(*body), title="◈ regression diff", title_align="left",
                        border_style=head[2], box=box.ROUNDED, padding=(1, 2)))


def cmd_versions(session: Session, args: dict) -> None:
    """/versions   list saved runs."""
    versions = store.list_versions()
    if not versions:
        console.print("  [dim]no saved versions yet — /run name=v1 to make one[/dim]")
        return
    t = Table(box=box.SIMPLE_HEAVY, header_style=f"bold {BRAND}", expand=False, pad_edge=False)
    t.add_column("version", style=f"bold {BRAND}")
    t.add_column("target", style="dim")
    t.add_column("landed", justify="right", style="red")
    t.add_column("pack", style="dim")
    for v in versions:
        t.add_row(v["version"], v["target"], str(v["counts"]["success"]), v["pack_hash"][:12])
    console.print(t)


def cmd_status(session: Session, args: dict) -> None:
    """/status   the current target and saved versions."""
    _render_target(session.target)
    cmd_versions(session, {})


def _render_api_status(cfg: "attack_api.ApiConfig") -> None:
    """Show attack-API integration state and, when not usable, how to enable it."""
    if cfg.is_configured:
        t = Table.grid(padding=(0, 3))
        t.add_column(style="dim", justify="right", width=9)
        t.add_column()
        t.add_row("source", f"[bold {BRAND}]api[/bold {BRAND}]  [dim]· fresh packs from the attack generator[/dim]")
        t.add_row("url", f"{cfg.url}")
        t.add_row("key", f"[green]{cfg.masked_key}[/green]")
        t.add_row("", "")
        t.add_row("use", "[bold]/run name=v1 source=api[/bold]  [dim]· or add mode=regression│discovery[/dim]")
        console.print(Panel(t, title="◈ attack API — connected", title_align="left",
                            border_style="green", box=box.ROUNDED, padding=(1, 2), expand=False))
        return
    body = Group(*[Text.from_markup(ln) for ln in attack_api.coming_soon_lines(cfg)])
    console.print(Panel(body, title="◈ attack API — paid add-on", title_align="left",
                        border_style=BRAND, box=box.ROUNDED, padding=(1, 2), expand=False))


def cmd_api(session: Session, args: dict) -> None:
    """/api   show attack-API integration status (free prebuilt vs paid generation)."""
    _render_api_status(attack_api.ApiConfig.from_env())


HELP = f"""
[bold {BRAND}]asrt-bench[/bold {BRAND}] [dim]— fire a pack, verify what lands, diff versions[/dim]

  [bold]/target[/bold] [dim]<name>│list[/dim]     choose the system under test
  [bold]/run[/bold] [dim]name=v1 [source=api][/dim]  fire the pack at it, save as version v1
  [bold]/diff[/bold] [dim]<v1> <v2>[/dim]         what changed between two versions
  [bold]/versions[/bold]              list saved runs
  [bold]/status[/bold]                current target + versions
  [bold]/api[/bold]                   attack-API integration (free prebuilt vs paid generation)
  [bold]/help[/bold]  [bold]/quit[/bold]

  [dim]tools are inert — nothing is emailed, written, queried, or executed for real[/dim]
"""

COMMANDS = {
    "target": cmd_target, "run": cmd_run, "diff": cmd_diff,
    "versions": cmd_versions, "status": cmd_status, "api": cmd_api,
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


def _welcome() -> None:
    from asrtbench import __version__
    logo = Text(_LOGO, style=f"bold {BRAND}")
    tag = Text("Automated Safety Regression Testing", style="bold white")
    sub = Text("fire a pack at an agent · verify what lands · diff versions", style="dim")
    steps = Text.from_markup(
        f"  [bold]1[/bold] [dim]/target[/dim] fixture      "
        f"[bold]2[/bold] [dim]/run[/dim] name=v1      "
        f"[bold]3[/bold] [dim]/diff[/dim] v1 v2")
    console.print(Panel(
        Group(Align.center(logo), Align.center(tag), Text(""), Align.center(sub),
              Text(""), Rule(style="grey30"), Text(""), steps),
        border_style=BRAND, box=box.ROUNDED, padding=(1, 3),
        subtitle=f"[dim]v{__version__} · /help · /quit[/dim]"))
    console.print()


def main() -> None:
    _welcome()
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
            console.print(f"  [red]✗ unknown command '{cmd}'[/red]  [dim]· try /help[/dim]")
            continue
        try:
            fn(session, args)
        except Exception as exc:
            console.print_exception(max_frames=6)
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]\n")


if __name__ == "__main__":
    main()
