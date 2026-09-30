"""Visible Chrome sessions with a saved login per site, human pacing and block detection."""

from __future__ import annotations

import os
import random
import re
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright
from rich.console import Console

from .config import SITE_LABELS, Settings

CHROME_PATHS = [
    Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]

LOGIN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "indeed": "https://secure.indeed.com/auth",
    "zip_recruiter": "https://www.ziprecruiter.com/authn/login",
}
CREDENTIAL_ENV = {
    "linkedin": ("LINKEDIN_EMAIL", "LINKEDIN_PASSWORD"),
    "indeed": ("INDEED_EMAIL", None),
    "zip_recruiter": ("ZIPRECRUITER_EMAIL", "ZIPRECRUITER_PASSWORD"),
}

LOGIN_URL = re.compile(r"linkedin\.com/(login|uas/login|authwall|signup)|secure\.indeed\.com/auth|ziprecruiter\.com/(authn|login)", re.I)
CHALLENGE_URL = re.compile(r"checkpoint|/challenge|captcha|/sorry/", re.I)
CHALLENGE_TITLE = re.compile(r"just a moment|security (check|verification)|attention required|access denied|captcha|are you a robot|verify", re.I)
CHALLENGE_TEXT = re.compile(
    r"unusual activity|verify (that )?you are (a )?human|let's do a quick security check|are you a robot|"
    r"press (and|&) hold|additional verification required|complete the security check",
    re.I,
)
CHALLENGE_FRAME = re.compile(r"challenges\.cloudflare\.com|hcaptcha|recaptcha|arkoselabs|funcaptcha", re.I)


def chrome_path() -> Path | None:
    return next((p for p in CHROME_PATHS if p.exists()), None)


def beep() -> None:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        print("\a", end="", flush=True)


class BrowserSession:
    """One visible Chrome window using the saved profile for one site."""

    def __init__(self, site: str, settings: Settings, console: Console) -> None:
        self.site = site
        self.settings = settings
        self.console = console
        self.profile_dir = settings.browser_profiles_dir / site
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "BrowserSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        kwargs = dict(user_data_dir=str(self.profile_dir), headless=False, no_viewport=True, args=["--start-maximized"])
        if chrome_path():
            kwargs["channel"] = "chrome"
        try:
            self.context = self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:
            self._pw.stop()
            raise RuntimeError(
                f"Couldn't open the browser ({exc}). If a window from an earlier run is still open, close it and retry."
            ) from exc
        self.context.set_default_timeout(15_000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()

    # ---- pacing ---------------------------------------------------------------

    def pause(self) -> None:
        time.sleep(random.uniform(*self.settings.apply.click_delay_seconds))

    def short_pause(self) -> None:
        time.sleep(random.uniform(*self.settings.apply.field_delay_seconds))

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        self.pause()

    def switch_to(self, page: Page) -> None:
        self.page = page
        page.bring_to_front()

    # ---- block / login detection ------------------------------------------------

    def blocked_reason(self) -> str | None:
        page = self.page
        try:
            url = page.url
            if CHALLENGE_URL.search(url):
                return "security check page"
            if LOGIN_URL.search(url):
                return "login page (your saved session may have expired)"
            if CHALLENGE_TITLE.search(page.title() or ""):
                return "security check page"
            for frame in page.frames:
                if CHALLENGE_FRAME.search(frame.url or ""):
                    return "CAPTCHA"
            body = page.locator("body").inner_text(timeout=3_000)
            # Short pages only: long pages are job posts that may mention "security check".
            if len(body) < 3_000 and CHALLENGE_TEXT.search(body):
                return "security check page"
        except Exception:
            return None
        return None

    def guard(self) -> bool:
        """If a CAPTCHA/check/login page is showing, hand control to the user.

        Returns False if it's still blocked after the user has had a go.
        """
        for _ in range(3):
            reason = self.blocked_reason()
            if not reason:
                return True
            beep()
            self.console.print(
                f"\n[bold red]{SITE_LABELS[self.site]} is showing a {reason}.[/] The agent won't try to get past it. "
                "Please deal with it in the browser window yourself."
            )
            input("Press Enter here when the normal page is showing again... ")
            self.page.wait_for_timeout(1_500)
        return self.blocked_reason() is None

    # ---- login ---------------------------------------------------------------------

    def login(self) -> bool:
        self.goto(LOGIN_URLS[self.site])
        email_env, password_env = CREDENTIAL_ENV[self.site]
        email = os.environ.get(email_env or "", "").strip()
        password = os.environ.get(password_env or "", "").strip() if password_env else ""
        if email:
            self._fill_first("input#username, input[type=email], input[name*=email i], input[autocomplete=username]", email)
        if password:
            self._fill_first("input[type=password]", password)
        self.console.print(
            f"\nSign in to [bold]{SITE_LABELS[self.site]}[/] in the browser window "
            + ("(your email/password are pre-filled - check them and click Sign in). " if email else "")
            + "Complete any verification code or CAPTCHA yourself."
        )
        input("Press Enter here once you're fully signed in... ")
        logged_in = not LOGIN_URL.search(self.page.url) and not CHALLENGE_URL.search(self.page.url)
        return logged_in

    def _fill_first(self, selector: str, value: str) -> None:
        try:
            field = self.page.locator(selector).first
            field.wait_for(state="visible", timeout=8_000)
            field.fill(value)
            self.short_pause()
        except Exception:
            pass  # the user can type it themselves
