"""Run the apply step across sites, respecting daily caps."""

from __future__ import annotations

import json
import random
import time

from rich.console import Console

from .appliers import APPLIERS, ApplyResult
from .browser import BrowserSession
from .config import SITE_LABELS, Settings
from .documents import Documents
from .llm import Claude
from .pipeline import ensure_cover_letter
from .report import export_excel
from .store import Job, Store, now_iso


def record(store: Store, job: Job, result: ApplyResult) -> None:
    qa_json = json.dumps(result.qa, ensure_ascii=False) if result.qa else None
    if result.outcome == "applied":
        store.update(job.id, status="applied", applied_at=now_iso(), apply_notes=result.notes, qa_json=qa_json)
    elif result.outcome in ("skipped", "external", "failed"):
        store.update(job.id, status=result.outcome, apply_notes=result.notes, qa_json=qa_json)
    elif result.outcome == "dry_run":
        store.update(job.id, apply_notes=result.notes, qa_json=qa_json)
    # blocked / quit: leave the job queued for next time


def run_apply(
    settings: Settings,
    store: Store,
    claude: Claude,
    docs: Documents,
    console: Console,
    site: str | None = None,
    min_score: int | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    job_id: str | None = None,
) -> None:
    missing = settings.missing_profile_fields()
    if missing:
        console.print("[red]Fill these in profile.yaml before applying:[/] " + ", ".join(missing))
        return
    if not docs.cv_uploadable:
        console.print("[yellow]Your CV isn't a PDF or DOCX, so it can't be uploaded to applications.[/]")

    min_score = settings.apply.min_score if min_score is None else min_score
    if job_id:
        job = store.get(job_id)
        if not job:
            console.print(f"[red]No job with id {job_id}.[/]")
            return
        queues = {job.site: [job]}
    else:
        sites = [site] if site else settings.search.sites
        queues = {s: store.apply_queue(min_score, s) for s in sites}

    if not any(queues.values()):
        console.print(f"No scored jobs at or above {min_score} waiting to be applied to. Run `python main.py search` first.")
        return

    stop = False
    for s, queue in queues.items():
        if stop or not queue:
            continue
        label = SITE_LABELS[s]
        cap = settings.apply.daily_caps.get(s, 10)
        used = store.applied_today(s)
        room = len(queue) if dry_run else max(0, cap - used)
        if room == 0:
            console.print(f"{label}: daily cap reached ({used}/{cap}). Skipping.")
            continue
        count = min(len(queue), room, limit or len(queue))
        console.rule(f"[bold]{label}[/]: {count} job(s)" + (" (dry run)" if dry_run else f", {used}/{cap} used today"))

        with BrowserSession(s, settings, console) as session:
            applier = APPLIERS[s](session, store, claude, settings, docs, console)
            for i, job in enumerate(queue[:count], start=1):
                console.print(f"\n[bold]{i}/{count}[/] {job.title} at {job.company} (score {job.score})")
                cover = None
                try:
                    with console.status("Writing a tailored cover letter..."):
                        cover = ensure_cover_letter(job, store, claude, settings)
                except Exception as exc:
                    console.print(f"[yellow]  No cover letter: {exc}[/]")
                try:
                    result = applier.apply(job, cover, dry_run)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    applier.screenshot(job)
                    result = ApplyResult("failed", f"error: {type(exc).__name__}: {str(exc)[:250]}")
                record(store, job, result)
                applier.cleanup()
                console.print(f"  -> [bold]{result.outcome}[/]" + (f" ({result.notes})" if result.notes else ""))
                if result.outcome in ("quit", "blocked"):
                    stop = True
                    break
                if i < count and result.outcome not in ("external",):
                    wait = random.uniform(*settings.apply.between_applications_seconds)
                    with console.status(f"Pausing {wait:.0f}s before the next job (Ctrl+C to stop)..."):
                        time.sleep(wait)
        export_excel(store)
    console.print(f"\nDone. {claude.usage.summary()}")
