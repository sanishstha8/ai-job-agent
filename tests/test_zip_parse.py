import json

from playwright.sync_api import sync_playwright

from jobagent.zip_search import JOB_LINK, LINKS_JS, POSTING_JS, parse_posting

POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Data Analyst",
    "hiringOrganization": {"@type": "Organization", "name": "Acme Health"},
    "jobLocation": [{"@type": "Place", "address": {"addressLocality": "New York", "addressRegion": "NY"}}],
    "description": "<p>Build <b>SQL</b> dashboards.</p><ul><li>Python</li></ul>",
    "datePosted": "2026-09-24T10:00:00Z",
    "employmentType": ["FULL_TIME"],
    "baseSalary": {"@type": "MonetaryAmount", "value": {"minValue": 80000, "maxValue": 95000, "unitText": "YEAR"}},
    "url": "https://www.ziprecruiter.com/c/Acme-Health/Job/Data-Analyst/-in-New-York,NY?jid=abc123&lvk=xyz",
}

RESULTS_PAGE = """<html><body>
<a href="https://www.ziprecruiter.com/c/Acme-Health/Job/Data-Analyst/-in-New-York,NY?jid=abc123&lvk=1">Data Analyst</a>
<a href="https://www.ziprecruiter.com/jobs/beta-co-1a2b3c/senior-analyst?lvk=2">Senior Analyst</a>
<a href="https://www.ziprecruiter.com/candidate/profile">Profile</a>
</body></html>"""


def test_parse_posting_from_json_ld():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(f'<html><head><script type="application/ld+json">{json.dumps(POSTING)}</script></head>'
                         "<body><main>ignored</main></body></html>")
        data = page.evaluate(POSTING_JS)
        page.set_content(RESULTS_PAGE)
        links = [h for h in page.evaluate(LINKS_JS) if JOB_LINK.search(h)]
        browser.close()

    job = parse_posting(data, "https://www.ziprecruiter.com/x")
    assert job.title == "Data Analyst" and job.company == "Acme Health"
    assert job.location == "New York, NY"
    assert "**SQL**" in job.description and "Python" in job.description
    assert job.salary_text() == "$80,000-$95,000 / yearly"
    assert job.job_type == "full_time" and job.date_posted == "2026-09-24"
    assert job.job_url.endswith("?jid=abc123")  # tracking params dropped
    assert len(links) == 2  # the two job links, not the profile link
