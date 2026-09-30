"""Scoring and cover-letter steps that sit between searching and applying."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

from .config import Settings
from .cover_letters import save_cover_letter
from .llm import Claude, JobScore, LLMError
from .store import Job, Store


def score_pending(store: Store, claude: Claude, console: Console, workers: int = 4) -> int:
    """Score every job with status 'new'. Returns how many were scored."""
    pending = store.jobs("new")
    if not pending:
        console.print("No new jobs to score.")
        return 0

    # Worker threads only call Claude; all database writes happen here on the
    # main thread (a SQLite connection can't be shared across threads).
    def score_one(job: Job) -> tuple[Job, JobScore | None, str | None]:
        try:
            return job, claude.score_job(job), None
        except (LLMError, anthropic.APIError) as exc:
            return job, None, f"{type(exc).__name__}: {exc}"

    done = failed = 0

    def save(job: Job, result: JobScore | None, error: str | None) -> None:
        nonlocal done, failed
        if result is None:
            failed += 1
            console.print(f"[yellow]  Couldn't score '{job.title}' at {job.company}: {error} (will retry next run)[/]")
            return
        store.update(
            job.id, status="scored", score=result.score, verdict=result.verdict, score_json=result.model_dump_json()
        )
        done += 1

    columns = (TextColumn("Scoring jobs"), BarColumn(), MofNCompleteColumn(), TextColumn("{task.description}"))
    with Progress(*columns, console=console, transient=True) as progress:
        task = progress.add_task("", total=len(pending))
        # Score the first job alone so the CV/profile prompt cache is written
        # before the parallel calls start reading it.
        first, rest = pending[0], pending[1:]
        save(*score_one(first))
        progress.advance(task)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(score_one, job) for job in rest]
            for future in as_completed(futures):
                save(*future.result())
                progress.advance(task)
                progress.update(task, description=claude.usage.summary())
    console.print(f"Scored {done} job(s)" + (f", {failed} failed" if failed else "") + f". {claude.usage.summary()}")
    return done


def ensure_cover_letter(job: Job, store: Store, claude: Claude, settings: Settings) -> Path:
    if job.cover_letter_path and Path(job.cover_letter_path).exists():
        return Path(job.cover_letter_path)
    text = claude.write_cover_letter(job)
    path = save_cover_letter(job, text, settings)
    store.update(job.id, cover_letter_path=str(path))
    job.cover_letter_path = str(path)
    return path
