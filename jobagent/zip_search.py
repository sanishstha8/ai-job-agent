"""ZipRecruiter search through your own visible, signed-in browser.

ZipRecruiter's bot protection blocks JobSpy's direct requests (HTTP 403), so the
agent opens the normal search page in Chrome instead - just like you would - and
reads each job's standard JobPosting data (the schema.org markup job sites publish
for Google Jobs). If ZipRecruiter shows a security check, you complete it yourself.
"""

from __future__ import annotations

import math
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from markdownify import markdownify
from rich.console import Console

from .browser import BrowserSession
from .config import Settings
from .store import Job

JOB_LINK = re.compile(r"ziprecruiter\.com/(c/[^/]+/Job/|jobs/[^/?#]+/[^/?#]+|job/[^/?#]+)", re.I)
INTERVALS = {"YEAR": "yearly", "MONTH": "monthly", "WEEK": "weekly", "DAY": "daily", "HOUR": "hourly"}

LINKS_JS = """() => {
  const out = new Set();
  for (const a of document.querySelectorAll('a[href]')) out.add(a.href);
  for (const s of document.querySelectorAll("script[type='application/ld+json']")) {
    try {
      const d = JSON.parse(s.textContent);
      for (const it of [].concat(d['@graph'] || d)) {
        for (const e of (it && it.itemListElement) || []) {
          if (e.url) out.add(e.url);
          if (e.item && e.item.url) out.add(e.item.url);
        }
        if (it && it['@type'] === 'JobPosting' && it.url) out.add(it.url);
      }
    } catch (e) {}
  }
  return Array.from(out);
}"""

POSTING_JS = """() => {
  for (const s of document.querySelectorAll("script[type='application/ld+json']")) {
    try {
      const d = JSON.parse(s.textContent);
      for (const it of [].concat(d['@graph'] || d)) if (it && it['@type'] === 'JobPosting') return it;
    } catch (e) {}
  }
  const canonical = document.querySelector("link[rel=canonical]");
  const main = document.querySelector("main") || document.body;
  return {fallback: true, title: (document.querySelector("h1") || {}).innerText || "",
          pageTitle: document.title, description: main.innerText.slice(0, 20000),
          url: canonical ? canonical.href : location.href};
}"""


def _first(value):
    return value[0] if isinstance(value, list) and value else value


def _clean_url(url: str) -> str:
    parts = urlsplit(url)
    keep = "&".join(q for q in parts.query.split("&") if q.startswith("jid="))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, keep, ""))


def parse_posting(data: dict, url: str) -> Job | None:
    if not data:
        return None
    if data.get("fallback"):
        title = (data.get("title") or "").strip()
        page_title = data.get("pageTitle") or ""
        # "Data Analyst Job in New York, NY at Acme Corp | ZipRecruiter" and similar
        company = ""
        if m := re.search(r"\bat (.+?)(\s*[|-]\s*ZipRecruiter)?$", page_title):
            company = m.group(1).strip()
        if not title:
            return None
        return Job(site="zip_recruiter", title=title, company=company, job_url=_clean_url(data.get("url") or url),
                   description=data.get("description") or "")

    org = _first(data.get("hiringOrganization")) or {}
    place = _first(data.get("jobLocation")) or {}
    address = (place.get("address") if isinstance(place, dict) else {}) or {}
    location = ", ".join(x for x in (address.get("addressLocality"), address.get("addressRegion")) if x)
    salary = data.get("baseSalary") or {}
    value = salary.get("value") if isinstance(salary, dict) else {}
    value = value if isinstance(value, dict) else {}
    lo = value.get("minValue") or value.get("value")
    hi = value.get("maxValue")
    return Job(
        site="zip_recruiter",
        title=str(data.get("title") or "").strip(),
        company=str(org.get("name") if isinstance(org, dict) else org or "").strip(),
        job_url=_clean_url(data.get("url") or url),
        location=location,
        description=markdownify(data.get("description") or "").strip(),
        date_posted=(data.get("datePosted") or "")[:10] or None,
        is_remote=(data.get("jobLocationType") == "TELECOMMUTE") or None,
        job_type=str(_first(data.get("employmentType")) or "").lower() or None,
        salary_min=float(lo) if lo else None,
        salary_max=float(hi) if hi else None,
        salary_interval=INTERVALS.get(str(value.get("unitText") or "").upper()),
    )


def search_zip_browser(settings: Settings, terms: list[str], limit: int, console: Console) -> dict[str, list[Job]]:
    s = settings.search
    results: dict[str, list[Job]] = {}
    with BrowserSession("zip_recruiter", settings, console) as session:
        for term in terms:
            params = {"search": term, "location": s.location or "", "radius": s.distance_miles}
            if s.hours_old:
                params["days"] = max(1, math.ceil(s.hours_old / 24))
            if s.remote_only:
                params["refine_by_location_type"] = "only_remote"
            session.goto("https://www.ziprecruiter.com/jobs-search?" + urlencode(params))
            if not session.guard():
                console.print("[yellow]  ZipRecruiter search stopped at a security check.[/]")
                break
            links: list[str] = []
            for _ in range(5):
                for href in session.page.evaluate(LINKS_JS):
                    if JOB_LINK.search(href) and _clean_url(href) not in {_clean_url(x) for x in links}:
                        links.append(href)
                if len(links) >= limit:
                    break
                session.page.mouse.wheel(0, 5000)
                session.page.wait_for_timeout(1_500)
            jobs: list[Job] = []
            with console.status(f"Reading {min(len(links), limit)} ZipRecruiter job pages for '{term}'..."):
                for link in links[:limit]:
                    try:
                        session.goto(link)
                    except Exception:
                        continue
                    if not session.guard():
                        break
                    if "ziprecruiter.com" not in session.page.url:
                        continue  # redirected to a company site
                    job = parse_posting(session.page.evaluate(POSTING_JS), session.page.url)
                    if job and job.title:
                        jobs.append(job)
            if not links:
                console.print("[yellow]  No job links found on the ZipRecruiter results page "
                              "(the page layout may have changed).[/]")
            results[term] = jobs
    return results
