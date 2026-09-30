"""Claude API calls: score jobs, write cover letters, answer application questions."""

from __future__ import annotations

import json
import threading
from typing import Any, Literal

import anthropic
import yaml
from pydantic import BaseModel, Field

from .config import SITE_LABELS, Settings
from .documents import Documents
from .search import compact_description
from .store import Job

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that get server-side refusal fallbacks (a refused request is retried on
# another model inside the same API call).
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}
# Models that don't accept output_config.effort.
NO_EFFORT_PREFIXES = ("claude-haiku-4-5",)
# USD per million tokens: (input, output). Cache reads cost 0.1x input, cache writes 1.25x.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


class LLMError(RuntimeError):
    pass


# ---- structured output schemas ---------------------------------------------


class JobScore(BaseModel):
    score: int = Field(description="Fit from 0 to 100")
    verdict: Literal["strong", "good", "weak", "no"]
    summary: str = Field(description="One sentence on the overall fit")
    reasons: list[str] = Field(description="Up to 4 short reasons the job fits")
    missing_requirements: list[str] = Field(description="Posting requirements the CV doesn't show")
    red_flags: list[str] = Field(description="Warning signs evident in the posting, if any")


class FieldAnswer(BaseModel):
    id: str
    answer: str = Field(description="Text to type, or the chosen option text")
    chosen_options: list[str] = Field(description="For select/radio/checkbox_group: options copied verbatim")
    confidence: Literal["high", "medium", "low"]
    needs_human: bool
    reason: str = Field(description="Short justification, citing CV or profile")


class FieldAnswers(BaseModel):
    answers: list[FieldAnswer]


# ---- prompts ---------------------------------------------------------------

SYSTEM_TEMPLATE = """You are a job-search assistant working for one candidate. You evaluate job postings for them, write their cover letters and fill in their job applications.

Ground rules for every task:
- Be truthful. Use only skills, experience, credentials and facts found in the candidate's CV or profile below. Never invent, inflate or imply anything they haven't done.
- Work authorization, visa sponsorship, citizenship, criminal history, demographic (EEO) and salary facts come only from the profile. Never infer them from the CV.
- Write in plain, natural US English.

<candidate_cv>
{cv}
</candidate_cv>

<candidate_profile>
{profile}
</candidate_profile>

<target_job_description>
This is what the candidate is looking for, in their own words:
{target}
</target_job_description>

<sample_cover_letter>
A cover letter the candidate wrote before. Use it as the model for their voice and style:
{letter}
</sample_cover_letter>"""

SCORE_PROMPT = """Score how well this job fits the candidate, from 0 to 100. Judge two things together: can the candidate credibly get this job based on their CV, and is it the kind of job they said they want in the target job description?

Calibration:
- 85-100 (verdict "strong"): clearly qualified and it matches what they want. Apply first.
- 70-84 ("good"): qualified for most of it and a reasonable match.
- 50-69 ("weak"): partial match, noticeable gaps, or not quite what they want.
- 0-49 ("no"): poor fit.
Cap the score at 40 if the job hits one of the candidate's deal-breakers, or has a hard requirement they clearly lack (a license, a clearance, or far more years of experience than they have).

reasons: up to 4 short points. missing_requirements: requirements in the posting the CV doesn't show. red_flags: only warning signs actually evident in the posting (for example commission-only pay, staffing agency, unrealistic requirements for the level). Empty lists are fine.

{job}"""

COVER_LETTER_PROMPT = """Write a cover letter from the candidate for this job.

- Match the voice, tone and structure of the sample cover letter, but write it fresh for this role and company.
- 250-350 words, 3 or 4 paragraphs.
- Open with "Dear Hiring Manager," unless the posting names the person to address.
- Connect two or three specific needs in the posting to specific evidence from the CV.
- Use the real company and job names. No placeholders, no brackets.
- End with a short sign-off and the candidate's full name ({name}).
- Output only the letter, starting with the greeting. No date, address block, subject line or commentary.

{job}"""

QUESTIONS_PROMPT = """You are filling in a job application form for the candidate. Answer every field listed in <fields>.

Rules:
- Eligibility and legal questions (work authorization, visa sponsorship, citizenship, criminal record), EEO/demographic questions and salary: answer only from the profile. If the profile doesn't clearly answer it, set needs_human to true.
- "How many years of experience with X": a whole number. Use profile.experience.skills_years when X is listed there. Otherwise count conservatively from dates in the CV, or 0 if X isn't in the CV.
- select and radio fields: chosen_options holds exactly one option, copied verbatim from that field's options; put the same text in answer.
- checkbox_group fields: chosen_options holds every option that truthfully applies, copied verbatim.
- checkbox (a single tick box): answer "Yes" to tick it or "No" to leave it unticked.
- number fields: digits only.
- text fields: what a recruiter expects for that label, such as a city, a URL or a short phrase. For open questions in a textarea (for example "Why do you want this job?"), write 2-4 sentences grounded in the CV and this posting.
- An honest "No" beats a false "Yes". Never overstate the candidate to get past a filter.
- confidence: "high" when the CV or profile states it directly, "medium" for a reasonable inference, "low" for a guess. Set needs_human to true whenever you would be guessing a fact only the candidate knows.
- Return one entry per field, using that field's id.

{job}

<fields>
{fields}
</fields>"""


def format_job(job: Job) -> str:
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company}",
        f"Location: {job.location or 'not stated'}" + (" (remote)" if job.is_remote else ""),
        f"Site: {SITE_LABELS.get(job.site, job.site)}",
    ]
    if job.job_type:
        parts.append(f"Job type: {job.job_type}")
    if salary := job.salary_text():
        parts.append(f"Salary: {salary}")
    body = compact_description(job.description) or "(no description)"
    return "<job_posting>\n" + "\n".join(parts) + f"\n\n{body}\n</job_posting>"


# ---- usage / cost tracking -------------------------------------------------


class Usage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.input = self.output = self.cache_read = self.cache_write = 0
        self.cost = 0.0

    def add(self, model: str, usage: Any) -> None:
        inp = usage.input_tokens or 0
        out = usage.output_tokens or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        price_in, price_out = next(
            (p for prefix, p in PRICES.items() if model.startswith(prefix)), PRICES["claude-opus-5"]
        )
        cost = (inp * price_in + out * price_out + read * price_in * 0.1 + write * price_in * 1.25) / 1e6
        with self._lock:
            self.calls += 1
            self.input += inp
            self.output += out
            self.cache_read += read
            self.cache_write += write
            self.cost += cost

    def summary(self) -> str:
        return (
            f"{self.calls} Claude calls, ~${self.cost:.2f} "
            f"(input {self.input:,}, cached reads {self.cache_read:,}, output {self.output:,} tokens)"
        )


# ---- client ----------------------------------------------------------------


class Claude:
    def __init__(self, settings: Settings, docs: Documents) -> None:
        self.settings = settings
        self.client = anthropic.Anthropic(max_retries=4)
        profile_yaml = yaml.safe_dump(settings.profile, sort_keys=False, allow_unicode=True)
        system_text = SYSTEM_TEMPLATE.format(
            cv=docs.cv_text,
            profile=profile_yaml.strip(),
            target=docs.target_job_text,
            letter=docs.cover_letter_text,
        )
        # One stable system block shared by every call, so the CV etc. are cached.
        self.system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
        self.usage = Usage()
        self._fallbacks_supported = True

    def _call(self, model: str, effort: str, max_tokens: int, prompt: str, output_format: type | None = None):
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=self.system,
            messages=[{"role": "user", "content": prompt}],
        )
        if not model.startswith(NO_EFFORT_PREFIXES):
            kwargs["output_config"] = {"effort": effort}
        use_fallbacks = self._fallbacks_supported and model in FALLBACK_MODELS
        if use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            if output_format is not None:
                response = self.client.beta.messages.parse(output_format=output_format, **kwargs)
            else:
                response = self.client.beta.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            if use_fallbacks and "fallback" in str(exc).lower():
                # This account/model doesn't accept fallbacks: continue without them.
                self._fallbacks_supported = False
                return self._call(model, effort, max_tokens, prompt, output_format)
            raise
        self.usage.add(model, response.usage)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMError(f"Claude declined this request ({getattr(details, 'category', None) or 'refusal'})")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off (max_tokens reached)")
        return response

    def score_job(self, job: Job) -> JobScore:
        response = self._call(
            self.settings.models.scoring, "low", 8000, SCORE_PROMPT.format(job=format_job(job)), JobScore
        )
        if response.parsed_output is None:
            raise LLMError("No score returned")
        result: JobScore = response.parsed_output
        result.score = max(0, min(100, result.score))
        return result

    def write_cover_letter(self, job: Job) -> str:
        personal = self.settings.profile.get("personal") or {}
        name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip() or "the candidate"
        response = self._call(
            self.settings.models.writing,
            "high",
            16000,
            COVER_LETTER_PROMPT.format(job=format_job(job), name=name),
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise LLMError("Empty cover letter returned")
        return text

    def answer_fields(self, job: Job, fields: list[dict[str, Any]]) -> dict[str, FieldAnswer]:
        """fields: [{id, label, kind, options, required}] -> {id: FieldAnswer}"""
        if not fields:
            return {}
        response = self._call(
            self.settings.models.answers,
            "medium",
            16000,
            QUESTIONS_PROMPT.format(job=format_job(job), fields=json.dumps(fields, indent=1)),
            FieldAnswers,
        )
        if response.parsed_output is None:
            raise LLMError("No answers returned")
        return {a.id: a for a in response.parsed_output.answers}

    def ping(self) -> str:
        model = self.settings.models.scoring
        extra = {} if model.startswith(NO_EFFORT_PREFIXES) else {"output_config": {"effort": "low"}}
        response = self.client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            **extra,
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()
