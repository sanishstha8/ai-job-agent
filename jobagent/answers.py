"""Answer common application questions straight from profile.yaml, and match answers to options."""

from __future__ import annotations

import re
from typing import Any

from .store import normalize_text

DECLINE = re.compile(
    r"decline|don.?t wish|do not wish|prefer not|not to (say|answer|disclose|self)|choose not|rather not|"
    r"do not want|don.?t want|not disclose",
    re.I,
)


def _yn(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return "Yes" if value.strip().lower() in ("yes", "true", "y") else "No"
    return "Yes" if value else "No"


def _s(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def rule_answer(label: str, kind: str, profile: dict[str, Any]) -> str | None:
    """Answer from the profile when the question clearly maps to a profile field."""
    L = (label or "").lower()
    if not L:
        return None
    personal = profile.get("personal") or {}
    work = profile.get("work_eligibility") or {}
    prefs = profile.get("preferences") or {}
    exp = profile.get("experience") or {}
    eeo = profile.get("eeo") or {}

    for item in profile.get("custom_answers") or []:
        pattern = str(item.get("match") or "")
        if pattern and re.search(pattern, L, re.I):
            return _s(item.get("answer"))

    # Voluntary self-identification
    if re.search(r"hispanic|latino", L):
        return _s(eeo.get("hispanic_latino"))
    if re.search(r"\brace\b|ethnicity", L):
        return _s(eeo.get("race_ethnicity"))
    if re.search(r"\bgender\b|\bsex\b", L):
        return _s(eeo.get("gender"))
    if re.search(r"veteran", L):
        return _s(eeo.get("veteran_status"))
    if re.search(r"disabilit", L):
        return _s(eeo.get("disability_status"))

    # Eligibility
    if re.search(r"sponsor", L):
        now, future = work.get("require_sponsorship_now"), work.get("require_sponsorship_future")
        if re.search(r"future", L) and not re.search(r"\bnow\b|current", L):
            return _yn(future)
        if now is None or future is None:
            return _yn(now) if re.search(r"\bnow\b|current", L) else None
        return _yn(now or future)
    if re.search(r"authori[sz]ed to work|eligible to work|work authori[sz]ation|right to work|legally (able|permitted)", L):
        return _yn(work.get("authorized_to_work_in_us"))
    if re.search(r"18 years|age of 18|at least 18|over 18", L):
        return _yn(work.get("over_18"))
    if re.search(r"relocat", L):
        return _yn(work.get("willing_to_relocate"))
    if re.search(r"commut", L):
        return _yn(work.get("comfortable_commuting"))
    if re.search(r"on-?site|in[- ]office|in person|hybrid", L) and re.search(r"comfortable|willing|able to|open to", L):
        return _yn(work.get("comfortable_onsite"))
    if re.search(r"background (check|screen)", L):
        return _yn(work.get("ok_with_background_check"))
    if re.search(r"drug (test|screen)", L):
        return _yn(work.get("ok_with_drug_test"))
    if re.search(r"driver.?s?.? licen[cs]e", L):
        return _yn(work.get("has_drivers_license"))
    if "clearance" in L:
        clearance = _s(work.get("security_clearance")) or "None"
        if kind in ("radio", "select", "checkbox") and re.search(r"\b(do|have|hold|possess|active)\b", L):
            return "No" if clearance.lower() in ("none", "no", "n/a") else "Yes"
        return clearance
    if re.search(r"citizen", L) and kind not in ("radio", "select", "checkbox"):
        return _s(work.get("citizenship_status"))

    # Years of experience with a named skill
    if re.search(r"years|how long", L) and re.search(r"experience|worked|using|work(ing)? with", L):
        for skill, years in (exp.get("skills_years") or {}).items():
            if re.search(rf"(?<![a-z0-9]){re.escape(str(skill).lower())}(?![a-z0-9])", L):
                return str(int(float(years or 0)))

    # Language proficiency
    for language, level in (profile.get("languages") or {}).items():
        if str(language).lower() in L and re.search(r"proficien|level|fluen|speak", L):
            return _s(level)

    if kind in ("radio", "checkbox", "checkbox_group"):
        return None  # everything below is typed text or a dropdown value

    # Contact details
    if re.search(r"first name|given name", L):
        return _s(personal.get("first_name"))
    if re.search(r"last name|surname|family name", L):
        return _s(personal.get("last_name"))
    if re.search(r"full name|^name\*?$|^your name|legal name", L):
        full = f"{personal.get('first_name') or ''} {personal.get('last_name') or ''}".strip()
        return full or None
    if re.search(r"e-?mail", L):
        return _s(personal.get("email"))
    if re.search(r"country code|phone country", L):
        return _s(personal.get("phone_country"))
    if re.search(r"phone|mobile|cell", L):
        return _s(personal.get("phone"))
    if re.search(r"linkedin", L):
        return _s(personal.get("linkedin_url"))
    if re.search(r"github", L):
        return _s(personal.get("github_url"))
    if re.search(r"portfolio|website|personal (site|url)", L):
        return _s(personal.get("portfolio_url"))
    if re.search(r"\bzip\b|postal", L):
        return _s(personal.get("zip_code"))
    if re.search(r"street|address line|^address", L):
        return _s(personal.get("address"))
    if re.search(r"\bcity\b|^location\b|current location", L):
        city, state = _s(personal.get("city")), _s(personal.get("state"))
        if re.search(r"\bcity\b", L) and not re.search(r"state", L) and kind != "combobox":
            return city
        return ", ".join(x for x in (city, state) if x) or None
    if re.search(r"^state\b|\bstate( or province)?$|state/province", L):
        return _s(personal.get("state"))

    # Preferences
    if re.search(r"salary|compensation|pay (expectation|requirement)|desired pay|expected pay", L):
        return _s(prefs.get("desired_salary"))
    if re.search(r"notice period", L):
        if kind == "number":
            return _s(prefs.get("notice_period_days"))
        return _s(prefs.get("earliest_start"))
    if re.search(r"start date|when can you start|available to start|earliest (start|date)", L):
        return _s(prefs.get("earliest_start"))

    # Education
    if re.search(r"highest (level of )?(education|degree)", L):
        return _s(exp.get("highest_degree"))
    return None


_RANGE = re.compile(r"(\d+)\s*(?:-|–|to)\s*(\d+)")
_AT_LEAST = re.compile(r"(\d+)\s*\+|(\d+)\s*(?:years?\s*)?or more|more than\s*(\d+)|over\s*(\d+)")
_LESS = re.compile(r"less than\s*(\d+)|under\s*(\d+)|fewer than\s*(\d+)")


def match_option(answer: str | None, options: list[str]) -> str | None:
    """Pick the option that best matches an answer, or None if unsure."""
    if not answer or not options:
        return None
    a = normalize_text(answer)
    normalized = [(o, normalize_text(o)) for o in options]
    for option, n in normalized:
        if n == a:
            return option
    if DECLINE.search(answer):
        for option, _ in normalized:
            if DECLINE.search(option):
                return option
    if a in ("yes", "no"):
        hits = [o for o, n in normalized if n == a or n.startswith(a + " ")]
        if len(hits) == 1:
            return hits[0]
    if a.isdigit():
        value = int(a)
        for option, n in normalized:
            if m := _RANGE.search(option):
                if int(m.group(1)) <= value <= int(m.group(2)):
                    return option
        for option, n in normalized:
            if (m := _AT_LEAST.search(option)) and value >= int(next(g for g in m.groups() if g)):
                return option
            if (m := _LESS.search(option)) and value < int(next(g for g in m.groups() if g)):
                return option
    starts = [o for o, n in normalized if n and (n.startswith(a) or a.startswith(n))]
    if len(starts) == 1:
        return starts[0]
    contains = [o for o, n in normalized if a and a in n]
    if len(contains) == 1:
        return contains[0]
    return None
