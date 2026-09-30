"""Load config.yaml, profile.yaml and .env into typed settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
INPUTS_DIR = ROOT / "inputs"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
COVER_LETTER_DIR = OUTPUT_DIR / "cover_letters"
SCREENSHOT_DIR = OUTPUT_DIR / "screenshots"
LOG_DIR = OUTPUT_DIR / "logs"
DB_PATH = DATA_DIR / "jobs.db"

SITES = ("linkedin", "indeed", "zip_recruiter")
SITE_ALIASES = {
    "linkedin": "linkedin",
    "indeed": "indeed",
    "zip_recruiter": "zip_recruiter",
    "ziprecruiter": "zip_recruiter",
    "zip": "zip_recruiter",
}
SITE_LABELS = {"linkedin": "LinkedIn", "indeed": "Indeed", "zip_recruiter": "ZipRecruiter"}

# Profile fields that must be filled before applying (section, key).
REQUIRED_PROFILE_FIELDS = [
    ("personal", "first_name"),
    ("personal", "last_name"),
    ("personal", "email"),
    ("personal", "phone"),
    ("personal", "city"),
    ("personal", "state"),
    ("work_eligibility", "authorized_to_work_in_us"),
    ("work_eligibility", "require_sponsorship_now"),
    ("work_eligibility", "require_sponsorship_future"),
]


def normalize_site(name: str) -> str:
    key = name.strip().lower().replace("-", "_")
    if key not in SITE_ALIASES:
        raise ValueError(f"Unknown site '{name}'. Use one of: linkedin, indeed, ziprecruiter")
    return SITE_ALIASES[key]


class SearchConfig(BaseModel):
    sites: list[str] = Field(default_factory=lambda: list(SITES))
    search_terms: list[str]
    location: str = ""
    distance_miles: int = 50
    remote_only: bool = False
    job_type: str | None = None
    hours_old: int | None = 72
    results_wanted: int = 25


class FilterConfig(BaseModel):
    title_must_include: list[str] = Field(default_factory=list)
    title_exclude: list[str] = Field(default_factory=list)
    company_blocklist: list[str] = Field(default_factory=list)
    description_exclude: list[str] = Field(default_factory=list)
    min_salary: float = 0


class ModelConfig(BaseModel):
    scoring: str = "claude-opus-5"
    writing: str = "claude-opus-5"
    answers: str = "claude-opus-5"


class ApplyConfig(BaseModel):
    min_score: int = 75
    daily_caps: dict[str, int] = Field(
        default_factory=lambda: {"linkedin": 15, "indeed": 10, "zip_recruiter": 10}
    )
    click_delay_seconds: tuple[float, float] = (1.5, 6.0)
    field_delay_seconds: tuple[float, float] = (0.5, 1.5)
    between_applications_seconds: tuple[float, float] = (30, 90)
    linkedin_follow_company: bool = False
    browser_profiles_dir: str | None = None


class Settings(BaseModel):
    search: SearchConfig
    filters: FilterConfig = Field(default_factory=FilterConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    profile: dict[str, Any] = Field(default_factory=dict)

    @property
    def browser_profiles_dir(self) -> Path:
        if self.apply.browser_profiles_dir:
            return Path(self.apply.browser_profiles_dir).expanduser()
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        return Path(base) / "jobagent" / "browser_profiles"

    def profile_value(self, section: str, key: str) -> Any:
        return (self.profile.get(section) or {}).get(key)

    def missing_profile_fields(self) -> list[str]:
        missing = []
        for section, key in REQUIRED_PROFILE_FIELDS:
            value = self.profile_value(section, key)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(f"{section}.{key}")
        return missing


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path.name} in {path.parent}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings(root: Path = ROOT) -> Settings:
    load_dotenv(root / ".env")
    raw = _load_yaml(root / "config.yaml")
    raw["profile"] = _load_yaml(root / "profile.yaml")
    settings = Settings.model_validate(raw)
    settings.search.sites = [normalize_site(s) for s in settings.search.sites]
    return settings


def ensure_dirs() -> None:
    for d in (DATA_DIR, OUTPUT_DIR, COVER_LETTER_DIR, SCREENSHOT_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
