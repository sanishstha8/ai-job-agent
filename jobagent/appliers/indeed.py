"""Indeed Apply (the on-Indeed application flow at smartapply.indeed.com). Best-effort."""

from __future__ import annotations

import re

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import Applier


class IndeedApplier(Applier):
    site = "indeed"
    NEXT = re.compile(r"^(continue|next|review your application|review)\b", re.I)
    SUBMIT = re.compile(r"^submit (your )?application\b", re.I)
    SUCCESS = re.compile(
        r"application has been submitted|your application has been sent|you've applied|application submitted", re.I
    )
    APPLY = re.compile(r"^apply now\b|^easily apply|^apply with indeed", re.I)
    EXTERNAL = re.compile(r"apply on (the )?company('s)? (site|website)|apply on employer", re.I)
    ALREADY_APPLIED = re.compile(r"^applied$|you applied", re.I)

    def open_apply(self, job) -> str:
        self.session.goto(job.job_url)
        if not self.session.guard():
            return "blocked"
        body = self.page.locator("body")
        if self.find_button(self.ALREADY_APPLIED, body):
            return "applied"
        button = self.page.locator("#indeedApplyButton:visible").first
        if not button.count():
            button = self.find_button(self.APPLY, body)
        if not button:
            return "external" if self.find_button(self.EXTERNAL, body) else "unavailable"
        try:
            with self.page.context.expect_page(timeout=6_000) as new_page:
                button.click()
            self.session.switch_to(new_page.value)
        except PlaywrightTimeout:
            pass  # opened in the same tab
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except PlaywrightTimeout:
            pass
        self.session.pause()
        if not self.session.guard():
            return "blocked"
        return "form"

    def container(self):
        return self.page.locator("body")
