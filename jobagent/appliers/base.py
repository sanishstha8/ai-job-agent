"""The apply flow shared by every site: open the job, step through the form, review, submit."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Locator
from rich.console import Console

from ..browser import BrowserSession, beep
from ..config import SCREENSHOT_DIR, Settings
from ..documents import Documents
from ..llm import Claude
from ..review import ask_field, confirm, pick_entry_to_edit, review_gate
from ..store import Job, Store
from .form_filler import FormFiller

MAX_STEPS = 25


@dataclass
class ApplyResult:
    # applied | skipped | external | failed | dry_run | blocked | quit
    outcome: str
    notes: str = ""
    qa: list[dict[str, Any]] = field(default_factory=list)


class Applier:
    """Override the class attributes and the open/close hooks per site."""

    site = ""
    NEXT = re.compile(r"^(next|continue|review|review your application|save and continue)\b", re.I)
    SUBMIT = re.compile(r"^submit( your)? application\b|^submit$", re.I)
    SUCCESS = re.compile(
        r"application (was |has been )?(sent|submitted)|you applied|thanks for applying|thank you for applying", re.I
    )
    SCAN_EXCLUDE = ""

    def __init__(
        self,
        session: BrowserSession,
        store: Store,
        claude: Claude,
        settings: Settings,
        docs: Documents,
        console: Console,
    ) -> None:
        self.session = session
        self.store = store
        self.claude = claude
        self.settings = settings
        self.docs = docs
        self.console = console
        self.filler = FormFiller(session, store, claude, settings, docs, console, self.SCAN_EXCLUDE)

    @property
    def page(self):
        return self.session.page

    # ---- hooks for each site ---------------------------------------------------------

    def open_apply(self, job: Job) -> str:
        """Open the job and start the apply flow.

        Returns 'form', 'one_click', 'applied' (already applied), 'external',
        'unavailable' or 'blocked'.
        """
        raise NotImplementedError

    def container(self) -> Locator:
        dialog = self.page.locator("[role='dialog']:visible")
        return dialog.last if dialog.count() else self.page.locator("body")

    def resume_upload_ok(self, container: Locator) -> bool:
        """False when the site already has the CV selected, to avoid duplicate uploads."""
        name = self.docs.cv_path.name if self.docs.cv_path else ""
        try:
            return not (name and name.lower() in container.inner_text().lower())
        except Exception:
            return True

    def before_submit(self, container: Locator) -> None:
        pass

    def after_submit(self) -> None:
        pass

    def abandon(self) -> None:
        """Leave a half-finished application without submitting it."""
        pass

    def click_one_click(self) -> None:
        raise NotImplementedError

    def cleanup(self) -> None:
        """Close any extra tabs the site opened, and go back to the first one."""
        pages = self.page.context.pages
        for extra in pages[1:]:
            try:
                extra.close()
            except Exception:
                pass
        if pages:
            self.session.page = pages[0]

    # ---- helpers -----------------------------------------------------------------------

    def find_button(self, pattern: re.Pattern, scope: Locator | None = None) -> Locator | None:
        scope = scope or self.container()
        for role in ("button", "link"):
            candidates = scope.get_by_role(role, name=pattern)
            try:
                for i in range(candidates.count()):
                    button = candidates.nth(i)
                    if button.is_visible() and button.is_enabled():
                        return button
            except Exception:
                continue
        return None

    def screenshot(self, job: Job) -> Path | None:
        path = SCREENSHOT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{job.site}_{job.id[:6]}.png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path), full_page=False)
            return path
        except Exception:
            return None

    def _signature(self) -> str:
        try:
            text = self.container().inner_text(timeout=3_000)
        except Exception:
            text = ""
        return hashlib.md5((self.page.url + text).encode("utf-8", "ignore")).hexdigest()

    def _succeeded(self, timeout_ms: int = 12_000) -> bool:
        waited = 0
        while waited < timeout_ms:
            try:
                if self.page.get_by_text(self.SUCCESS).first.is_visible():
                    return True
            except Exception:
                pass
            self.page.wait_for_timeout(1_000)
            waited += 1_000
        return False

    # ---- the flow ----------------------------------------------------------------------

    def apply(self, job: Job, cover_letter: Path | None, dry_run: bool = False) -> ApplyResult:
        state = self.open_apply(job)
        if state == "blocked":
            return ApplyResult("blocked", "stopped at a security check or login page")
        if state == "applied":
            return ApplyResult("applied", "already applied (the site shows it as applied)")
        if state == "external":
            return ApplyResult("external", "applies on the company's own website")
        if state == "unavailable":
            return ApplyResult("failed", "no apply button found (job closed or page changed)")
        if state == "one_click":
            return self._one_click_flow(job, cover_letter, dry_run)
        return self._form_flow(job, cover_letter, dry_run)

    def _form_flow(self, job: Job, cover_letter: Path | None, dry_run: bool) -> ApplyResult:
        qa: list[dict[str, Any]] = []
        retry = False
        stuck = 0
        for _ in range(MAX_STEPS):
            if not self.session.guard():
                return ApplyResult("blocked", "stopped at a security check", qa)
            container = self.container()
            self.filler.fill_page(container, job, qa, cover_letter, self.resume_upload_ok, retry=retry)

            if self.find_button(self.SUBMIT, container):
                return self._review_and_submit(job, qa, cover_letter, dry_run)

            next_button = self.find_button(self.NEXT, container)
            if not next_button:
                return self._manual(job, qa, cover_letter, "The agent can't find the Next or Submit button.")
            before = self._signature()
            next_button.click()
            self.session.pause()
            if self._signature() == before:
                stuck += 1
                retry = True
                if stuck >= 3:
                    return self._manual(job, qa, cover_letter, "The site keeps rejecting this step.")
                self.console.print("[yellow]The site didn't accept that step; checking which answers it flagged.[/]")
            else:
                stuck, retry = 0, False
        return self._manual(job, qa, cover_letter, "This form has more steps than expected.")

    def _review_and_submit(
        self, job: Job, qa: list[dict[str, Any]], cover_letter: Path | None, dry_run: bool, note: str | None = None
    ) -> ApplyResult:
        container = self.container()
        self.before_submit(container)
        while True:
            beep()
            choice = review_gate(self.console, job, qa, cover_letter, self.screenshot(job), note, dry_run)
            if choice == "dry":
                self.abandon()
                return ApplyResult("dry_run", "dry run: filled but not submitted", qa)
            if choice == "n":
                self.abandon()
                return ApplyResult("skipped", "skipped at review", qa)
            if choice == "q":
                self.abandon()
                return ApplyResult("quit", "", qa)
            if choice == "m":
                return self._manual(job, qa, cover_letter, None)
            if choice == "e":
                index = pick_entry_to_edit(self.console, qa)
                if index is None:
                    continue
                entry = qa[index]
                new_value = ask_field(self.console, entry["label"], entry["kind"], entry.get("options") or [],
                                      entry["answer"], "your current answer", True)
                if new_value is None:
                    continue
                if not self.filler.refill(self.container(), entry, new_value, qa):
                    self.console.print(
                        "[yellow]That question is on an earlier page, so it can't be changed from here. "
                        "Choose m to fix it in the browser yourself.[/]"
                    )
                continue
            # choice == "y"
            submit = self.find_button(self.SUBMIT, self.container())
            if not submit:
                return self._manual(job, qa, cover_letter, "The Submit button disappeared.")
            submit.click()
            self.session.pause()
            if self._succeeded():
                self.filler.remember(qa)
                self.after_submit()
                return ApplyResult("applied", "", qa)
            if confirm(self.console, "The agent couldn't see a confirmation. Was the application submitted?"):
                self.filler.remember(qa)
                self.after_submit()
                return ApplyResult("applied", "confirmed by you", qa)
            return self._manual(job, qa, cover_letter, "Submitting didn't seem to work.")

    def _one_click_flow(self, job: Job, cover_letter: Path | None, dry_run: bool) -> ApplyResult:
        note = (
            "This is a one-click application: clicking submit sends your saved profile and resume on this site "
            "straight away. The agent will answer any follow-up questions the site asks afterwards."
        )
        choice = review_gate(self.console, job, [], cover_letter, self.screenshot(job), note, dry_run)
        if choice == "dry":
            return ApplyResult("dry_run", "dry run: one-click job, not submitted")
        if choice in ("n", "q"):
            return ApplyResult("skipped" if choice == "n" else "quit", "skipped at review")
        if choice == "m":
            return self._manual(job, [], cover_letter, None)
        self.click_one_click()
        self.session.pause()
        if self._succeeded(6_000):
            self.after_submit()
            return ApplyResult("applied", "one-click apply")
        # Follow-up questions appeared: continue with the normal form flow.
        return self._form_flow(job, cover_letter, dry_run=False)

    def _manual(self, job: Job, qa: list[dict[str, Any]], cover_letter: Path | None, problem: str | None) -> ApplyResult:
        beep()
        if problem:
            self.console.print(f"\n[yellow]{problem}[/]")
        if cover_letter:
            self.console.print(f"Cover letter for this job: {cover_letter}")
        self.console.print("Finish this application in the browser window yourself (or close it to skip).")
        input("Press Enter here when you're done... ")
        if confirm(self.console, "Did you submit the application?"):
            self.filler.remember(qa)
            return ApplyResult("applied", "finished manually", qa)
        self.abandon()
        return ApplyResult("failed" if problem else "skipped", problem or "skipped (manual)", qa)
