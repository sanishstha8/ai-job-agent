"""SQLite storage for jobs, applications and remembered answers."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# Job statuses:
#   new        found, waiting to be scored
#   filtered   rejected by the keyword prefilter (see filter_reason)
#   duplicate  same company + title already found on another site
#   scored     scored by Claude, not applied yet
#   applied    application submitted
#   skipped    you chose N at the review screen
#   external   only applies on the company's own site - apply manually
#   failed     the agent couldn't complete the form (see apply_notes)
STATUSES = ("new", "filtered", "duplicate", "scored", "applied", "skipped", "external", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    job_url TEXT UNIQUE,
    job_url_direct TEXT,
    description TEXT,
    date_posted TEXT,
    is_remote INTEGER,
    job_type TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_interval TEXT,
    dedupe_key TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    filter_reason TEXT,
    score INTEGER,
    verdict TEXT,
    score_json TEXT,
    cover_letter_path TEXT,
    applied_at TEXT,
    apply_notes TEXT,
    qa_json TEXT,
    found_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key);
CREATE TABLE IF NOT EXISTS qa_memory (
    key TEXT PRIMARY KEY,
    label TEXT,
    kind TEXT,
    answer TEXT,
    updated_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_text(text: str | None) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_key(company: str | None, title: str | None) -> str:
    company = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company)\b", "", normalize_text(company))
    return f"{normalize_text(company)}|{normalize_text(title)}"


def job_id(job_url: str) -> str:
    return hashlib.sha1(job_url.encode("utf-8")).hexdigest()[:16]


@dataclass
class Job:
    site: str
    title: str
    company: str
    job_url: str
    location: str = ""
    job_url_direct: str | None = None
    description: str = ""
    date_posted: str | None = None
    is_remote: bool | None = None
    job_type: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_interval: str | None = None
    # Filled from the database
    id: str = ""
    status: str = "new"
    filter_reason: str | None = None
    score: int | None = None
    verdict: str | None = None
    score_json: str | None = None
    cover_letter_path: str | None = None
    applied_at: str | None = None
    apply_notes: str | None = None
    qa_json: str | None = None
    found_at: str | None = None
    updated_at: str | None = None
    dedupe_key: str = field(default="")

    def __post_init__(self) -> None:
        if not self.id:
            self.id = job_id(self.job_url)
        if not self.dedupe_key:
            self.dedupe_key = dedupe_key(self.company, self.title)

    @property
    def score_details(self) -> dict[str, Any]:
        return json.loads(self.score_json) if self.score_json else {}

    def salary_text(self) -> str:
        if not self.salary_min and not self.salary_max:
            return ""
        lo = f"${self.salary_min:,.0f}" if self.salary_min else ""
        hi = f"${self.salary_max:,.0f}" if self.salary_max else ""
        span = f"{lo}-{hi}" if lo and hi else lo or hi
        return f"{span} / {self.salary_interval}" if self.salary_interval else span


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- jobs -------------------------------------------------------------

    def add_job(self, job: Job) -> str:
        """Insert a job. Returns 'exists', 'duplicate' or 'new'."""
        if self.conn.execute("SELECT 1 FROM jobs WHERE id = ? OR job_url = ?", (job.id, job.job_url)).fetchone():
            return "exists"
        twin = self.conn.execute(
            "SELECT id, site FROM jobs WHERE dedupe_key = ? AND status != 'duplicate' LIMIT 1",
            (job.dedupe_key,),
        ).fetchone()
        if twin:
            job.status = "duplicate"
            job.filter_reason = f"same job as {twin['id']} ({twin['site']})"
        job.found_at = job.updated_at = now_iso()
        row = asdict(job)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", list(row.values()))
        self.conn.commit()
        return job.status if job.status == "duplicate" else "new"

    def get(self, job_id_: str) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id_,)).fetchone()
        return Job(**dict(row)) if row else None

    def update(self, job_id_: str, **fields: Any) -> None:
        if not fields:
            return
        unknown = set(fields) - {f for f in Job.__dataclass_fields__}
        if unknown:
            raise ValueError(f"Unknown job fields: {unknown}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"Unknown status {fields['status']!r}")
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*fields.values(), job_id_])
        self.conn.commit()

    def jobs(self, status: str | Iterable[str] | None = None, site: str | None = None) -> list[Job]:
        sql, args = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            statuses = [status] if isinstance(status, str) else list(status)
            sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
            args += statuses
        if site:
            sql += " AND site = ?"
            args.append(site)
        sql += " ORDER BY COALESCE(score, -1) DESC, found_at DESC"
        return [Job(**dict(r)) for r in self.conn.execute(sql, args).fetchall()]

    def apply_queue(self, min_score: int, site: str | None = None) -> list[Job]:
        return [j for j in self.jobs("scored", site) if (j.score or 0) >= min_score]

    def applied_today(self, site: str) -> int:
        today = datetime.now().date().isoformat()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE site = ? AND status = 'applied' AND applied_at LIKE ?",
            (site, f"{today}%"),
        ).fetchone()
        return int(row[0])

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        return {r[0]: r[1] for r in rows}

    # ---- remembered answers ------------------------------------------------

    @staticmethod
    def qa_key(label: str, kind: str) -> str:
        return f"{kind}|{normalize_text(label)}"

    def remembered_answer(self, label: str, kind: str) -> str | None:
        row = self.conn.execute(
            "SELECT answer FROM qa_memory WHERE key = ?", (self.qa_key(label, kind),)
        ).fetchone()
        return row["answer"] if row else None

    def remember_answer(self, label: str, kind: str, answer: str) -> None:
        self.conn.execute(
            "INSERT INTO qa_memory (key, label, kind, answer, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET answer = excluded.answer, updated_at = excluded.updated_at",
            (self.qa_key(label, kind), label, kind, answer, now_iso()),
        )
        self.conn.commit()
