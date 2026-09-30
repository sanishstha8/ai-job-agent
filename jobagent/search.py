"""Search job boards with JobSpy (no login needed) and apply keyword prefilters."""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from rich.console import Console

from .config import SITE_LABELS, FilterConfig, Settings
from .store import Job, Store

ANNUALIZE = {"yearly": 1, "monthly": 12, "weekly": 52, "daily": 260, "hourly": 2080}


def _clean(value: Any) -> Any:
    """Turn pandas NaN/NaT into None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if str(value) in ("nan", "NaT", "None"):
        return None
    return value


def row_to_job(row: dict[str, Any], site: str) -> Job | None:
    row = {k: _clean(v) for k, v in row.items()}
    if not row.get("job_url") or not row.get("title"):
        return None
    return Job(
        site=site,
        title=str(row["title"]).strip(),
        company=str(row.get("company") or "").strip(),
        job_url=str(row["job_url"]),
        job_url_direct=row.get("job_url_direct"),
        location=str(row.get("location") or ""),
        description=str(row.get("description") or ""),
        date_posted=str(row["date_posted"]) if row.get("date_posted") else None,
        is_remote=bool(row["is_remote"]) if row.get("is_remote") is not None else None,
        job_type=str(row["job_type"]) if row.get("job_type") else None,
        salary_min=float(row["min_amount"]) if row.get("min_amount") else None,
        salary_max=float(row["max_amount"]) if row.get("max_amount") else None,
        salary_interval=row.get("interval"),
    )


def _contains_any(text: str, needles: list[str]) -> str | None:
    lowered = text.lower()
    for needle in needles:
        if needle.lower() in lowered:
            return needle
    return None


def prefilter(job: Job, filters: FilterConfig, remote_only: bool = False) -> str | None:
    """Return a reason to drop the job, or None to keep it."""
    title = job.title or ""
    if filters.title_must_include and not _contains_any(title, filters.title_must_include):
        return "title has none of title_must_include"
    if hit := _contains_any(f" {title} ", filters.title_exclude):
        return f"title contains '{hit.strip()}'"
    if hit := _contains_any(job.company or "", filters.company_blocklist):
        return f"company blocklisted ('{hit}')"
    if hit := _contains_any(job.description or "", filters.description_exclude):
        return f"description contains '{hit}'"
    if remote_only and job.is_remote is False and "remote" not in (job.location or "").lower():
        return "not remote"
    if filters.min_salary and (job.salary_max or job.salary_min):
        top = job.salary_max or job.salary_min
        factor = ANNUALIZE.get((job.salary_interval or "yearly").lower(), 1)
        if top * factor < filters.min_salary:
            return f"salary below {filters.min_salary:,.0f}"
    if not (job.description or "").strip():
        return "no description available"
    return None


def search_site(settings: Settings, site: str, term: str, limit: int | None) -> list[Job]:
    from jobspy import scrape_jobs

    # ZipRecruiter's 403s are expected and handled by the browser fallback.
    logging.getLogger("JobSpy:ZipRecruiter").setLevel(logging.CRITICAL)

    s = settings.search
    kwargs: dict[str, Any] = dict(
        site_name=[site],
        search_term=term,
        location=s.location or None,
        distance=s.distance_miles,
        results_wanted=limit or s.results_wanted,
        country_indeed="USA",
        description_format="markdown",
        verbose=0,
    )
    # Indeed can't combine hours_old with job_type/is_remote; those are then
    # checked by the prefilter instead.
    if s.hours_old:
        kwargs["hours_old"] = s.hours_old
    if not (site == "indeed" and s.hours_old):
        kwargs["is_remote"] = s.remote_only
        if s.job_type:
            kwargs["job_type"] = s.job_type
    if site == "linkedin":
        kwargs["linkedin_fetch_description"] = True

    df = scrape_jobs(**kwargs)
    jobs = []
    for row in df.to_dict("records"):
        job = row_to_job(row, site)
        if job:
            jobs.append(job)
    return jobs


def run_search(
    settings: Settings,
    store: Store,
    console: Console,
    sites: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Search every term on every site; store new jobs. Returns counts."""
    totals = {"found": 0, "new": 0, "duplicate": 0, "filtered": 0}
    terms = settings.search.search_terms
    for site in sites or settings.search.sites:
        label = SITE_LABELS[site]
        zip_blocked: list[str] = []
        for term in terms:
            with console.status(f"Searching {label} for '{term}'..."):
                try:
                    jobs = search_site(settings, site, term, limit)
                except Exception as exc:  # network errors, site blocks, parser changes
                    console.print(f"[yellow]  {label}: search failed ({type(exc).__name__}: {exc})[/]")
                    jobs = []
            if site == "zip_recruiter" and not jobs:
                zip_blocked.append(term)
                continue
            _store_results(store, settings, console, label, term, jobs, totals)

        if zip_blocked:
            from .zip_search import search_zip_browser

            console.print(f"  {label} blocked the quick search, so the agent will search in your Chrome window instead.")
            try:
                found = search_zip_browser(settings, zip_blocked, limit or settings.search.results_wanted, console)
            except Exception as exc:
                console.print(f"[yellow]  {label}: browser search failed ({type(exc).__name__}: {exc})[/]")
                found = {}
            for term, jobs in found.items():
                _store_results(store, settings, console, label, term, jobs, totals)
    return totals


def _store_results(
    store: Store, settings: Settings, console: Console, label: str, term: str, jobs: list[Job], totals: dict[str, int]
) -> None:
    new = dup = filt = 0
    for job in jobs:
        reason = prefilter(job, settings.filters, settings.search.remote_only)
        if reason:
            job.status, job.filter_reason = "filtered", reason
        result = store.add_job(job)
        if result == "exists":
            continue
        if result == "duplicate":
            dup += 1
        elif job.status == "filtered":
            filt += 1
        else:
            new += 1
    totals["found"] += len(jobs)
    totals["new"] += new
    totals["duplicate"] += dup
    totals["filtered"] += filt
    console.print(
        f"  {label} / '{term}': {len(jobs)} found, [green]{new} new[/], {filt} filtered out, {dup} duplicates"
    )


_WS = re.compile(r"\n{3,}")


def compact_description(text: str) -> str:
    """Collapse runs of blank lines; job boards pad descriptions heavily."""
    return _WS.sub("\n\n", text or "").strip()
