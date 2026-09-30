from datetime import datetime

from jobagent.config import FilterConfig
from jobagent.search import prefilter, row_to_job
from jobagent.store import Job


def make_job(url="https://x.com/1", site="linkedin", title="Data Analyst", company="Acme Inc", **kw):
    return Job(site=site, title=title, company=company, job_url=url, description=kw.pop("description", "Do SQL."), **kw)


def test_add_job_dedupes_by_url_and_company_title(store):
    assert store.add_job(make_job()) == "new"
    assert store.add_job(make_job()) == "exists"
    # Same job on another site -> duplicate
    assert store.add_job(make_job(url="https://indeed.com/9", site="indeed", company="ACME")) == "duplicate"
    assert store.counts() == {"new": 1, "duplicate": 1}


def test_apply_queue_orders_by_score_and_respects_min(store):
    for i, score in enumerate([60, 90, 80]):
        job = make_job(url=f"https://x.com/{i}", title=f"Analyst {i}")
        store.add_job(job)
        store.update(job.id, status="scored", score=score)
    assert [j.score for j in store.apply_queue(70)] == [90, 80]


def test_applied_today_counts_per_site(store):
    job = make_job()
    store.add_job(job)
    store.update(job.id, status="applied", applied_at=datetime.now().isoformat())
    assert store.applied_today("linkedin") == 1
    assert store.applied_today("indeed") == 0


def test_qa_memory_roundtrip(store):
    store.remember_answer("Are you willing to travel 25%?", "radio", "Yes")
    assert store.remembered_answer("are you willing to travel 25% ?", "radio") == "Yes"
    assert store.remembered_answer("Are you willing to travel 25%?", "select") is None


def test_prefilter_rules():
    f = FilterConfig(title_exclude=["senior", "sr."], company_blocklist=["Revature"], min_salary=70000,
                     description_exclude=["secret clearance"])
    assert prefilter(make_job(title="Senior Data Analyst"), f) == "title contains 'senior'"
    assert "blocklisted" in prefilter(make_job(company="Revature LLC"), f)
    assert "clearance" in prefilter(make_job(description="Active Secret Clearance required"), f)
    assert "salary" in prefilter(make_job(salary_max=25.0, salary_interval="hourly"), f)  # 52k/yr
    assert prefilter(make_job(salary_max=45.0, salary_interval="hourly"), f) is None  # 93.6k/yr
    assert prefilter(make_job(), FilterConfig(title_must_include=["engineer"])) is not None
    assert prefilter(make_job(is_remote=False, location="Austin, TX"), FilterConfig(), remote_only=True) == "not remote"


def test_row_to_job_handles_nan():
    job = row_to_job({"job_url": "https://x.com/5", "title": "Analyst", "company": float("nan"),
                      "min_amount": float("nan"), "is_remote": None, "description": "desc"}, "indeed")
    assert job.company == "" and job.salary_min is None and job.is_remote is None
