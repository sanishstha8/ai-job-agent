from rich.console import Console

from jobagent.llm import JobScore, LLMError, Usage
from jobagent.pipeline import score_pending
from jobagent.store import Job, Store


class FakeScorer:
    usage = Usage()

    def score_job(self, job):
        if "fail" in job.title:
            raise LLMError("boom")
        return JobScore(score=88, verdict="strong", summary="fits", reasons=["SQL"], missing_requirements=[], red_flags=[])


def test_score_pending_parallel_writes(tmp_path):
    # A file database, like the real one: SQLite connections refuse cross-thread use.
    store = Store(tmp_path / "jobs.db")
    for i in range(6):
        store.add_job(Job(site="indeed", title=f"Analyst {i}", company=f"Co {i}", job_url=f"https://e.com/{i}"))
    store.add_job(Job(site="indeed", title="will fail", company="X", job_url="https://e.com/fail"))

    assert score_pending(store, FakeScorer(), Console(quiet=True), workers=3) == 6
    assert store.counts() == {"scored": 6, "new": 1}  # the failed one stays queued for a retry
    assert {j.score for j in store.jobs("scored")} == {88}
    store.close()
