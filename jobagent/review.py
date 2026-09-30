"""Terminal screens: the review gate before submitting, and questions the agent can't answer alone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import SITE_LABELS
from .store import Job

SOURCE_STYLE = {
    "profile": "green",
    "memory": "cyan",
    "ai": "magenta",
    "you": "bold",
    "edited": "bold",
    "prefilled": "dim",
    "cover letter": "blue",
    "file": "blue",
}
SOURCE_HELP = (
    "[green]profile[/] = from profile.yaml, [cyan]memory[/] = your earlier answer, "
    "[magenta]ai[/] = Claude, [dim]prefilled[/] = already filled in by the site"
)


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def job_header(job: Job) -> str:
    score = f"score {job.score} ({job.verdict})" if job.score is not None else "not scored"
    return f"[bold]{job.title}[/] at [bold]{job.company}[/] - {SITE_LABELS.get(job.site, job.site)}, {score}\n{job.job_url}"


def ask_field(
    console: Console,
    label: str,
    kind: str,
    options: list[str],
    suggestion: str | list[str] | None,
    reason: str | None,
    required: bool,
) -> str | list[str] | None:
    """Ask the user to answer one form field. Returns None to leave it blank."""
    lines = [f"[bold]{label or '(unlabelled field)'}[/]  [dim]({kind}{', required' if required else ''})[/]"]
    for i, option in enumerate(options, start=1):
        lines.append(f"  {i}. {option}")
    if suggestion:
        shown = "; ".join(suggestion) if isinstance(suggestion, list) else suggestion
        lines.append(f"\nSuggested: [magenta]{_short(shown, 200)}[/]" + (f"  [dim]({_short(reason, 120)})[/]" if reason else ""))
    console.print(Panel("\n".join(lines), title="The agent needs your answer", border_style="yellow"))

    hint = "Enter = use suggestion" if suggestion else ("Enter = leave blank" if not required else "")
    while True:
        if options:
            multi = kind == "checkbox_group"
            raw = Prompt.ask(
                f"Option number{'s, comma-separated' if multi else ''}" + (f" ({hint})" if hint else ""),
                default="", show_default=False,
            ).strip()
            if not raw:
                if suggestion:
                    return suggestion
                if not required:
                    return None
                continue
            try:
                picks = [options[int(p) - 1] for p in raw.replace(" ", "").split(",") if p]
            except (ValueError, IndexError):
                console.print("[red]Type the number(s) shown above.[/]")
                continue
            return picks if multi else picks[0]
        if kind == "checkbox":
            default = str(suggestion or "").lower() in ("yes", "true")
            return "Yes" if Confirm.ask("Tick this box?", default=default) else "No"
        raw = Prompt.ask("Your answer" + (f" ({hint})" if hint else ""), default="", show_default=False).strip()
        if raw:
            return raw
        if suggestion:
            return suggestion
        if not required:
            return None


def review_gate(
    console: Console,
    job: Job,
    qa: list[dict[str, Any]],
    cover_letter: Path | None,
    screenshot: Path | None,
    note: str | None = None,
    dry_run: bool = False,
) -> str:
    """Show everything about to be submitted. Returns y / n / e / m / q (or 'dry')."""
    console.print()
    console.rule("[bold]Review before submitting[/]")
    console.print(job_header(job))
    if note:
        console.print(f"[yellow]{note}[/]")
    if qa:
        table = Table(show_lines=False, expand=True)
        table.add_column("#", width=3, justify="right")
        table.add_column("Question", ratio=3)
        table.add_column("Answer", ratio=3)
        table.add_column("Source", width=12)
        for i, entry in enumerate(qa, start=1):
            source = entry.get("source", "")
            table.add_row(
                str(i),
                _short(entry.get("label") or "(unlabelled)", 80),
                _short(entry.get("answer") or "", 80),
                f"[{SOURCE_STYLE.get(source, 'white')}]{source}[/]",
            )
        console.print(table)
        console.print(SOURCE_HELP)
    if cover_letter and cover_letter.exists():
        txt = cover_letter.with_suffix(".txt")
        preview = txt.read_text(encoding="utf-8") if txt.exists() else ""
        console.print(Panel(_short(preview, 400), title=f"Cover letter: {cover_letter.name}", border_style="blue"))
    if screenshot:
        console.print(f"Screenshot: {screenshot}")
    if dry_run:
        console.print("[yellow]Dry run: stopping here without submitting.[/]")
        return "dry"
    return Prompt.ask(
        "[bold]Submit?[/] [bold]y[/]=submit  [bold]n[/]=skip  [bold]e[/]=edit an answer  "
        "[bold]m[/]=I'll finish it in the browser  [bold]q[/]=quit",
        choices=["y", "n", "e", "m", "q"],
        case_sensitive=False,
    ).lower()


def pick_entry_to_edit(console: Console, qa: list[dict[str, Any]]) -> int | None:
    if not qa:
        console.print("There are no answers to edit on this application.")
        return None
    raw = Prompt.ask("Number of the answer to change (Enter = cancel)", default="", show_default=False).strip()
    if not raw:
        return None
    try:
        index = int(raw) - 1
        qa[index]
    except (ValueError, IndexError):
        console.print("[red]No answer with that number.[/]")
        return None
    return index


def confirm(console: Console, question: str, default: bool = False) -> bool:
    return Confirm.ask(question, default=default, console=console)
