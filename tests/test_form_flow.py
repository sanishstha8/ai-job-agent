"""End-to-end check of the form filler and apply flow against a local two-step form."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from rich.console import Console

from jobagent.appliers import base as base_module
from jobagent.appliers.base import Applier
from jobagent.llm import FieldAnswer
from jobagent.store import Job

FORM = """
<html><body>
<div id="step1">
  <label for="fn">First name *</label><input id="fn" required>
  <label for="em">Email address</label><input id="em" type="email" required>
  <label for="city">City</label><input id="city">
  <fieldset><legend>Will you now or in the future require sponsorship for employment visa status?</legend>
    <input type="radio" name="spons" id="s1" value="Yes" required><label for="s1">Yes</label>
    <input type="radio" name="spons" id="s2" value="No"><label for="s2">No</label>
  </fieldset>
  <label for="exp">How many years of experience do you have with Python?</label><input id="exp" type="number" required>
  <label for="gender">Gender</label>
  <select id="gender"><option value="">Select an option</option><option>Male</option><option>Female</option>
    <option>I don't wish to answer</option></select>
  <label for="deg">Do you have a Bachelor's degree?</label>
  <select id="deg" required><option value="">Select an option</option><option>Yes</option><option>No</option></select>
  <label for="nick">Nickname</label><input id="nick">
  <label for="why">Why do you want this job?</label><textarea id="why" required></textarea>
  <label for="resume">Upload resume</label><input type="file" id="resume" style="display:none">
  <p id="err" role="alert" style="display:none">Please fill in all required fields</p>
  <button type="button" id="next">Next</button>
</div>
<div id="step2" style="display:none">
  <fieldset aria-required="true"><legend>Which tools have you used?</legend>
    <input type="checkbox" id="t1"><label for="t1">SQL</label>
    <input type="checkbox" id="t2"><label for="t2">Tableau</label>
    <input type="checkbox" id="t3"><label for="t3">SAS</label>
  </fieldset>
  <input type="checkbox" id="agree" required><label for="agree">I certify the information is accurate</label>
  <button type="button" id="submit">Submit application</button>
</div>
<div id="done" style="display:none">Your application was sent!</div>
<script>
  const $ = (id) => document.getElementById(id);
  $("next").onclick = () => {
    const ok = $("fn").value && $("em").value && $("why").value && $("deg").value &&
               document.querySelector("input[name=spons]:checked") && $("resume").files.length;
    if (!ok) { $("err").style.display = "block"; return; }
    $("step1").style.display = "none"; $("step2").style.display = "block";
  };
  $("submit").onclick = () => {
    if (!$("agree").checked) return;
    $("step2").style.display = "none"; $("done").style.display = "block";
  };
</script>
</body></html>
"""

AI_ANSWERS = {
    "Do you have a Bachelor's degree?": ("Yes", ["Yes"]),
    "Why do you want this job?": ("I enjoy turning data into decisions.", []),
    "Which tools have you used?": ("SQL; Tableau", ["SQL", "Tableau"]),
    "I certify the information is accurate": ("Yes", []),
}


class FakeClaude:
    def __init__(self):
        self.asked = []

    def answer_fields(self, job, fields):
        out = {}
        for f in fields:
            self.asked.append(f["label"])
            answer, chosen = AI_ANSWERS[f["label"]]
            out[f["id"]] = FieldAnswer(id=f["id"], answer=answer, chosen_options=chosen,
                                       confidence="high", needs_human=False, reason="test")
        return out


class FakeDocs:
    def __init__(self, cv_path: Path):
        self.cv_path = cv_path
        self.cv_uploadable = True


class FakeSession:
    def __init__(self, page):
        self.page = page

    def pause(self):
        self.page.wait_for_timeout(50)

    def short_pause(self):
        pass

    def guard(self):
        return True

    def switch_to(self, page):
        self.page = page


class LocalFormApplier(Applier):
    site = "linkedin"

    def open_apply(self, job):
        self.page.set_content(FORM)
        return "form"


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def test_full_form_flow(page, settings, store, tmp_path, monkeypatch):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(base_module, "review_gate", lambda *a, **k: "y")
    monkeypatch.setattr(base_module, "beep", lambda: None)
    monkeypatch.setattr(base_module, "SCREENSHOT_DIR", tmp_path)

    claude = FakeClaude()
    applier = LocalFormApplier(FakeSession(page), store, claude, settings, FakeDocs(cv), Console(quiet=True))
    job = Job(site="linkedin", title="Data Analyst", company="Acme", job_url="https://example.com/job/1")

    result = applier.apply(job, cover_letter=None)

    assert result.outcome == "applied", result.notes
    assert page.get_by_text("Your application was sent!").is_visible()
    answers = {e["label"]: (e["answer"], e["source"]) for e in result.qa}
    assert answers["First name"] == ("Alex", "profile")
    assert answers["Email address"] == ("alex@example.com", "profile")
    assert answers["City"] == ("Austin", "profile")
    assert answers["Will you now or in the future require sponsorship for employment visa status?"] == ("No", "profile")
    assert answers["How many years of experience do you have with Python?"] == ("3", "profile")
    assert answers["Gender"] == ("I don't wish to answer", "profile")
    assert answers["Do you have a Bachelor's degree?"] == ("Yes", "ai")
    assert answers["Which tools have you used?"] == ("SQL; Tableau", "ai")
    assert answers["Upload CV"] == ("cv.pdf", "file")
    assert "Nickname" not in answers  # optional and unknown: left blank
    # Only the questions the profile can't answer went to Claude
    assert sorted(claude.asked) == sorted(AI_ANSWERS)
    # Approved AI answers are remembered for next time; long free text isn't
    assert store.remembered_answer("Do you have a Bachelor's degree?", "select") == "Yes"
    assert store.remembered_answer("Why do you want this job?", "textarea") is None


REJECTING_FORM = """
<html><body>
<div id="step1">
  <label for="yrs">Years of experience with Tableau</label><input id="yrs" type="number" required>
  <div><span id="yrs-err" class="error" style="display:none">Enter a whole number between 0 and 2</span></div>
  <button type="button" id="next">Next</button>
</div>
<div id="step2" style="display:none"><button type="button" id="submit">Submit application</button></div>
<div id="done" style="display:none">Application submitted</div>
<script>
  const $ = (id) => document.getElementById(id);
  $("next").onclick = () => {
    if (!(Number($("yrs").value) <= 2)) {
      $("yrs").setAttribute("aria-invalid", "true"); $("yrs-err").style.display = "block"; return;
    }
    $("step1").style.display = "none"; $("step2").style.display = "block";
  };
  $("submit").onclick = () => { $("step2").style.display = "none"; $("done").style.display = "block"; };
</script>
</body></html>
"""


def test_rejected_step_asks_the_user(page, settings, store, tmp_path, monkeypatch):
    from jobagent.appliers import form_filler

    class Claude5(FakeClaude):
        def answer_fields(self, job, fields):
            return {f["id"]: FieldAnswer(id=f["id"], answer="5", chosen_options=[], confidence="high",
                                         needs_human=False, reason="test") for f in fields}

    asked = []

    def fake_ask(console, label, kind, options, suggestion, reason, required):
        asked.append((label, suggestion, reason))
        return "2"

    class RejectingApplier(LocalFormApplier):
        def open_apply(self, job):
            self.page.set_content(REJECTING_FORM)
            return "form"

    monkeypatch.setattr(form_filler, "ask_field", fake_ask)
    monkeypatch.setattr(base_module, "review_gate", lambda *a, **k: "y")
    monkeypatch.setattr(base_module, "beep", lambda: None)
    monkeypatch.setattr(base_module, "SCREENSHOT_DIR", tmp_path)

    applier = RejectingApplier(FakeSession(page), store, Claude5(), settings, FakeDocs(tmp_path / "cv.pdf"),
                               Console(quiet=True))
    result = applier.apply(Job(site="linkedin", title="A", company="B", job_url="https://e.com/2"), None)

    assert result.outcome == "applied", result.notes
    assert asked and asked[0][0] == "Years of experience with Tableau"
    assert "whole number between 0 and 2" in asked[0][2]
    assert {e["label"]: e["answer"] for e in result.qa}["Years of experience with Tableau"] == "2"
