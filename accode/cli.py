"""Accode command-line interface."""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from accode.agent.events import ConsolePrompter, ConsoleSink
from accode.agent.llm import AgentLLM
from accode.agent.loop import run_agent
from accode.agent.permissions import PermissionGate
from accode.config import load_config
from accode.context import Context


@click.command()
@click.argument("goal", nargs=-1)
@click.option("--config", "config_path", default=None,
              help="Path to config.yaml (default: ./config.yaml if present).")
@click.option("--cwd", default=None,
              help="Working directory for the agent (default: current directory).")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Auto-approve every tool call (unattended mode).")
@click.option("--model", default=None,
              help="Override the agent-loop model for this run.")
def main(goal: tuple[str, ...], config_path: str | None,
         cwd: str | None, yes: bool, model: str | None) -> None:
    """Accode — an in-house coding agent.

    Run a one-off task:

        python -m accode "convert the hive repo at ../hive-repo to GCP and test it"

    Or start an interactive session by passing no GOAL.
    """
    console = Console()
    try:
        cfg = load_config(config_path)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/] {exc}")
        sys.exit(1)
    if model:
        cfg.setdefault("agent", {})["model"] = model

    work_dir = Path(cwd).resolve() if cwd else Path.cwd()
    if not work_dir.is_dir():
        console.print(f"[red]Error:[/] working directory not found: {work_dir}")
        sys.exit(1)

    try:
        llm = AgentLLM(cfg)
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        sys.exit(1)

    ctx = Context(cfg=cfg, cwd=work_dir, console=console)
    sink = ConsoleSink(console)
    gate = PermissionGate(cfg, auto_approve=yes, prompter=ConsolePrompter(console))

    console.print(Panel.fit(
        "[bold cyan]Accode[/] — in-house coding agent\n"
        f"[dim]cwd  :[/] {work_dir}\n"
        f"[dim]model:[/] {llm.model}",
        border_style="cyan",
    ))

    goal_text = " ".join(goal).strip()
    if goal_text:
        try:
            run_agent(goal_text, ctx, llm, gate, sink)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/]")
        return

    # interactive session
    console.print("[dim]Interactive session — enter a request, or 'exit' to quit.[/]\n")
    messages: list = []
    while True:
        try:
            line = input("accode> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if line.lower() in ("exit", "quit", ":q"):
            break
        if not line:
            continue
        try:
            messages = run_agent(line, ctx, llm, gate, sink, messages=messages)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted — continuing session.[/]")
        except Exception as exc:   # keep the REPL alive on a loop-level error
            console.print(f"[red]Error:[/] {exc}")


if __name__ == "__main__":
    main()
