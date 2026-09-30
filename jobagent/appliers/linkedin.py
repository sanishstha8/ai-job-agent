"""LinkedIn Easy Apply."""

from __future__ import annotations

import re

from playwright.sync_api import Locator

from .base import Applier


class LinkedInApplier(Applier):
    site = "linkedin"
    NEXT = re.compile(r"^(continue to next step|next|review( your application)?)$", re.I)
    SUBMIT = re.compile(r"^submit application$", re.I)
    SUCCESS = re.compile(r"application (was )?sent|your application was sent|application submitted", re.I)
    # Saved-resume cards: LinkedIn picks one itself; the agent only uploads when yours is missing.
    SCAN_EXCLUDE = ".jobs-document-upload-redesign-card__container, .jobs-resume-picker__resume, [class*='document-upload-redesign-card']"

    EASY_APPLY = re.compile(r"easy apply", re.I)
    EXTERNAL_APPLY = re.compile(r"^apply\b", re.I)
    ALREADY_APPLIED = re.compile(r"applied \d+ (second|minute|hour|day|week|month|year)s? ago|see application", re.I)
    CLOSED = re.compile(r"no longer accepting applications", re.I)

    def _main_text(self, pattern: re.Pattern) -> bool:
        try:
            return self.page.locator("main").get_by_text(pattern).first.is_visible()
        except Exception:
            return False

    def open_apply(self, job) -> str:
        self.session.goto(job.job_url)
        if not self.session.guard():
            return "blocked"
        try:
            self.page.locator(".jobs-apply-button, .jobs-s-apply, main").first.wait_for(timeout=15_000)
        except Exception:
            pass
        if self._main_text(self.ALREADY_APPLIED):
            return "applied"
        if self._main_text(self.CLOSED):
            return "unavailable"
        main = self.page.locator("main")
        easy = self.find_button(self.EASY_APPLY, main)
        if easy:
            easy.click()
            self.session.pause()
            if not self.session.guard():
                return "blocked"
            try:
                self.container().wait_for(state="visible", timeout=15_000)
            except Exception:
                return "unavailable"
            return "form"
        if self.find_button(self.EXTERNAL_APPLY, main):
            return "external"
        return "unavailable"

    def container(self) -> Locator:
        modal = self.page.locator(".jobs-easy-apply-modal:visible, [data-test-modal][role='dialog']:visible")
        if modal.count():
            return modal.last
        return super().container()

    def before_submit(self, container: Locator) -> None:
        if self.settings.apply.linkedin_follow_company:
            return
        box = container.locator("#follow-company-checkbox")
        try:
            if box.count() and box.is_checked():
                container.locator("label[for='follow-company-checkbox']").click()
                self.session.short_pause()
        except Exception:
            pass

    def after_submit(self) -> None:
        self.session.pause()
        button = self.find_button(re.compile(r"^(done|dismiss)$", re.I), self.page.locator("body"))
        if button:
            button.click()

    def abandon(self) -> None:
        try:
            dismiss = self.find_button(re.compile(r"^dismiss$", re.I), self.page.locator("body"))
            if dismiss:
                dismiss.click()
                self.session.short_pause()
            discard = self.find_button(re.compile(r"^discard$", re.I), self.page.locator("body"))
            if discard:
                discard.click()
        except Exception:
            pass
