"""AI job agent - search LinkedIn, Indeed and ZipRecruiter, rank jobs against your CV, and apply.

Usage (from this folder, with the virtual environment active):
    python main.py doctor                       check your setup
    python main.py login linkedin               sign in once (also: indeed, ziprecruiter)
    python main.py search                       find + score new jobs, update output/jobs.xlsx
    python main.py status                       counts and the top jobs waiting
    python main.py letters --limit 5            write cover letters for the top jobs
    python main.py apply --site linkedin --dry-run --limit 1
    python main.py apply --site linkedin        apply, reviewing each one before it's sent
    python main.py report                       rebuild output/jobs.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jobagent.config import DB_PATH, SITE_LABELS, ensure_dirs, load_settings, normalize_site

console = Console()


def _claude_and_docs(settings):
    from jobagent.documents import Documents
    from jobagent.llm import Claude

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        console.print("[red]ANTHROPIC_API_KEY is missing.[/] Copy .env.example to .env and add your key.")
        sys.exit(1)
    docs = Documents()
    return Claude(settings, docs), docs


def cmd_doctor(settings, store, args) -> None:
    ok = True

    def check(passed: bool, text: str, fix: str = "") -> None:
        nonlocal ok
        ok &= passed
        mark = "[green]OK  [/]" if passed else "[red]FIX [/]"
        console.print(f"{mark} {text}" + (f"\n      {fix}" if fix and not passed else ""))

    from jobagent.browser import chrome_path
    from jobagent.documents import Documents

    try:
        docs = Documents()
        check(True, f"Inputs read: CV {len(docs.cv_text):,} chars, cover letter {len(docs.cover_letter_text):,} chars, "
                    f"target job {len(docs.target_job_text):,} chars")
        check(docs.cv_uploadable, f"CV can be uploaded ({docs.cv_path.name})", "Use a PDF or DOCX for inputs/cv.*")
    except (FileNotFoundError, ValueError) as exc:
        check(False, "Input documents", str(exc))
        docs = None

    missing = settings.missing_profile_fields()
    check(not missing, "profile.yaml required fields", "Fill in: " + ", ".join(missing))
    check(bool(settings.search.search_terms), "config.yaml has search terms", "Add at least one search term")

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    check(has_key, "ANTHROPIC_API_KEY set", "Copy .env.example to .env and add your key")
    if has_key and docs:
        from jobagent.llm import Claude

        try:
            reply = Claude(settings, docs).ping()
            check(True, f"Claude API reachable ({settings.models.scoring} replied '{reply[:20]}')")
        except Exception as exc:
            check(False, "Claude API call", f"{type(exc).__name__}: {exc}")

    check(chrome_path() is not None, "Google Chrome found", "Install Chrome, or the agent will use Playwright's Chromium")
    pw_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    check(pw_dir.exists() and any(pw_dir.glob("chromium-*")), "Playwright browser installed",
          "Run: .venv\\Scripts\\python -m playwright install chromium")
    for site in settings.search.sites:
        profile = settings.browser_profiles_dir / site
        signed_in = profile.exists() and any(profile.iterdir())
        check(signed_in, f"{SITE_LABELS[site]} browser profile exists",
              f"Run: python main.py login {site.replace('_', '')}")

    counts = store.counts()
    if counts:
        console.print("Database: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    console.print("\n[bold green]All set.[/]" if ok else "\n[bold yellow]Fix the items above, then run doctor again.[/]")


def cmd_login(settings, store, args) -> None:
    from jobagent.browser import BrowserSession

    site = normalize_site(args.site)
    with BrowserSession(site, settings, console) as session:
        if session.login():
            console.print(f"[green]Signed in to {SITE_LABELS[site]}. The session is saved for future runs.[/]")
        else:
            console.print("[yellow]The browser still looks like it's on a login/verification page. "
                          "Run the login command again if applying fails.[/]")


def cmd_search(settings, store, args) -> None:
    from jobagent.report import export_excel
    from jobagent.search import run_search

    sites = [normalize_site(s) for s in args.sites] if args.sites else None
    claude = None
    if not args.no_score:
        claude, _ = _claude_and_docs(settings)  # fail early, before a long search
    totals = run_search(settings, store, console, sites=sites, limit=args.limit)
    console.print(f"Search done: {totals['found']} found, {totals['new']} new.")
    if claude:
        from jobagent.pipeline import score_pending

        score_pending(store, claude, console)
    path = export_excel(store)
    console.print(f"Spreadsheet: {path}")
    _print_top(store, settings.apply.min_score)


def cmd_score(settings, store, args) -> None:
    from jobagent.pipeline import score_pending
    from jobagent.report import export_excel

    claude, _ = _claude_and_docs(settings)
    score_pending(store, claude, console)
    console.print(f"Spreadsheet: {export_excel(store)}")


def cmd_letters(settings, store, args) -> None:
    from jobagent.pipeline import ensure_cover_letter
    from jobagent.report import export_excel

    claude, _ = _claude_and_docs(settings)
    min_score = settings.apply.min_score if args.min_score is None else args.min_score
    site = normalize_site(args.site) if args.site else None
    jobs = [j for j in store.jobs(("scored", "external"), site) if (j.score or 0) >= min_score][: args.limit]
    for job in jobs:
        with console.status(f"Writing cover letter: {job.title} at {job.company}..."):
            path = ensure_cover_letter(job, store, claude, settings)
        console.print(f"  {path}")
    console.print(f"{len(jobs)} cover letter(s). {claude.usage.summary()}")
    export_excel(store)


def cmd_apply(settings, store, args) -> None:
    from jobagent.runner import run_apply

    claude, docs = _claude_and_docs(settings)
    run_apply(
        settings, store, claude, docs, console,
        site=normalize_site(args.site) if args.site else None,
        min_score=args.min_score, limit=args.limit, dry_run=args.dry_run, job_id=args.job,
    )


def cmd_report(settings, store, args) -> None:
    from jobagent.report import export_excel

    console.print(f"Spreadsheet: {export_excel(store)}")


def cmd_status(settings, store, args) -> None:
    counts = store.counts()
    if not counts:
        console.print("No jobs yet. Run `python main.py search`.")
        return
    console.print("Jobs by status: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for site in settings.search.sites:
        cap = settings.apply.daily_caps.get(site, 10)
        console.print(f"  {SITE_LABELS[site]}: applied today {store.applied_today(site)}/{cap}")
    _print_top(store, settings.apply.min_score)


def _print_top(store, min_score: int, limit: int = 15) -> None:
    jobs = store.apply_queue(min_score)[:limit]
    if not jobs:
        console.print(f"No jobs scored {min_score}+ waiting to be applied to.")
        return
    table = Table(title=f"Top jobs waiting (score >= {min_score})")
    for col in ("ID", "Score", "Site", "Title", "Company", "Location"):
        table.add_column(col, overflow="fold")
    for job in jobs:
        table.add_row(job.id, str(job.score), SITE_LABELS.get(job.site, job.site), job.title, job.company, job.location)
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI job search & apply agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check your setup")
    p = sub.add_parser("login", help="sign in to a job site once; the session is saved")
    p.add_argument("site", help="linkedin | indeed | ziprecruiter")

    p = sub.add_parser("search", help="find new jobs and score them")
    p.add_argument("--sites", nargs="+", help="only these sites")
    p.add_argument("--limit", type=int, help="results per site per search term")
    p.add_argument("--no-score", action="store_true", help="search only; don't call Claude")

    sub.add_parser("score", help="score jobs that weren't scored yet")

    p = sub.add_parser("letters", help="write cover letters for the top jobs without applying")
    p.add_argument("--site")
    p.add_argument("--min-score", type=int)
    p.add_argument("--limit", type=int, default=5)

    p = sub.add_parser("apply", help="apply to the top jobs, with a review before each submit")
    p.add_argument("--site", help="linkedin | indeed | ziprecruiter (default: all in config.yaml)")
    p.add_argument("--min-score", type=int, help="override apply.min_score")
    p.add_argument("--limit", type=int, help="max applications per site this run")
    p.add_argument("--dry-run", action="store_true", help="fill forms but never submit")
    p.add_argument("--job", help="apply to one job by its ID (see `status`)")

    sub.add_parser("report", help="rebuild output/jobs.xlsx")
    sub.add_parser("status", help="show counts and the top jobs waiting")

    args = parser.parse_args()
    ensure_dirs()
    try:
        settings = load_settings()
    except Exception as exc:
        console.print(f"[red]Settings error:[/] {exc}")
        sys.exit(1)

    from jobagent.store import Store

    store = Store(DB_PATH)
    commands = {
        "doctor": cmd_doctor, "login": cmd_login, "search": cmd_search, "score": cmd_score,
        "letters": cmd_letters, "apply": cmd_apply, "report": cmd_report, "status": cmd_status,
    }
    try:
        commands[args.command](settings, store, args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)
    finally:
        store.close()


if __name__ == "__main__":
    main()
