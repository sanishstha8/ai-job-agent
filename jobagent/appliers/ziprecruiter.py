"""ZipRecruiter 1-Click / Quick Apply. Best-effort."""

from __future__ import annotations

import re

from .base import Applier


class ZipRecruiterApplier(Applier):
    site = "zip_recruiter"
    NEXT = re.compile(r"^(next|continue|review)\b", re.I)
    SUBMIT = re.compile(r"^(submit( application)?|send application|submit my application)$", re.I)
    SUCCESS = re.compile(r"application (was |has been )?(sent|submitted)|you applied|applied!|you've applied", re.I)
    ONE_CLICK = re.compile(r"1-click apply|one[- ]click apply", re.I)
    APPLY = re.compile(r"^(quick apply|apply now|apply|easy apply)$", re.I)
    EXTERNAL = re.compile(r"apply on (the )?company('s)? (site|website)|continue to (company|employer)", re.I)
    ALREADY_APPLIED = re.compile(r"^applied$|you applied", re.I)

    def open_apply(self, job) -> str:
        self.session.goto(job.job_url)
        if not self.session.guard():
            return "blocked"
        body = self.page.locator("body")
        if self.find_button(self.ALREADY_APPLIED, body):
            return "applied"
        if self.find_button(self.ONE_CLICK, body):
            return "one_click"
        if self.find_button(self.EXTERNAL, body):
            return "external"
        button = self.find_button(self.APPLY, body)
        if not button:
            return "unavailable"
        pages_before = len(self.page.context.pages)
        button.click()
        self.session.pause()
        pages = self.page.context.pages
        if len(pages) > pages_before:
            new = pages[-1]
            if "ziprecruiter.com" not in new.url:
                new.close()
                return "external"
            self.session.switch_to(new)
        if "ziprecruiter.com" not in self.page.url:
            self.page.go_back()
            return "external"
        if not self.session.guard():
            return "blocked"
        return "form"

    def click_one_click(self) -> None:
        button = self.find_button(self.ONE_CLICK, self.page.locator("body"))
        if button:
            button.click()

    def after_submit(self) -> None:
        close = self.find_button(re.compile(r"^(close|done|dismiss)$", re.I), self.page.locator("body"))
        if close:
            close.click()
