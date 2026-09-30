import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobagent.config import ApplyConfig, SearchConfig, Settings  # noqa: E402
from jobagent.store import Store  # noqa: E402

PROFILE = {
    "personal": {
        "first_name": "Alex", "last_name": "Rivera", "email": "alex@example.com", "phone": "5125550100",
        "city": "Austin", "state": "Texas", "zip_code": "78701", "linkedin_url": "https://linkedin.com/in/alex",
    },
    "work_eligibility": {
        "authorized_to_work_in_us": True, "require_sponsorship_now": False, "require_sponsorship_future": False,
        "security_clearance": "None", "over_18": True, "willing_to_relocate": False,
    },
    "preferences": {"desired_salary": "85000", "earliest_start": "Two weeks after offer", "notice_period_days": 14},
    "experience": {"total_years": 3, "highest_degree": "Bachelor's Degree", "skills_years": {"Python": 3, "SQL": 4}},
    "languages": {"English": "Native or bilingual"},
    "eeo": {"gender": "Decline to self-identify", "veteran_status": "I am not a protected veteran",
            "disability_status": "Decline to self-identify", "race_ethnicity": "Decline to self-identify"},
    "custom_answers": [{"match": "how did you (hear|learn)", "answer": "Job board"}],
}


@pytest.fixture
def settings():
    return Settings(
        search=SearchConfig(search_terms=["Data Analyst"]),
        apply=ApplyConfig(click_delay_seconds=(0, 0), field_delay_seconds=(0, 0), between_applications_seconds=(0, 0)),
        profile=PROFILE,
    )


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()
