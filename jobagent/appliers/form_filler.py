"""Site-agnostic form handling: find the fields on a page, work out answers, fill them in.

Answer order for each field: profile.yaml rules -> answers you approved before ->
Claude -> ask you in the terminal (when Claude is unsure or the fact is personal).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import anthropic
from playwright.sync_api import Locator
from rich.console import Console

from ..answers import match_option, rule_answer
from ..browser import BrowserSession
from ..config import Settings
from ..documents import Documents
from ..llm import Claude, FieldAnswer, LLMError
from ..review import ask_field
from ..store import Job, Store

TEXT_KINDS = {"text", "textarea", "number", "email", "tel", "url", "date", "combobox"}
CHOICE_KINDS = {"select", "radio"}
RESUME_RE = re.compile(r"resume|résumé|\bcv\b|curriculum", re.I)
COVER_RE = re.compile(r"cover[\s_-]*letter", re.I)
# Answers worth remembering for next time (short, factual ones only).
MEMORABLE_SOURCES = {"ai", "you", "edited"}
MEMORABLE_KINDS = {"select", "radio", "checkbox", "checkbox_group", "number", "text", "combobox", "email", "tel", "url"}

SCAN_JS = r"""
(root, exclude) => {
  const doc = root.ownerDocument, win = doc.defaultView;
  if (!win.__jaPrefix) { win.__jaPrefix = Math.random().toString(36).slice(2, 7); win.__jaN = 0; }
  const tag = (el) => { if (!el.dataset.ja) el.dataset.ja = win.__jaPrefix + "-" + (win.__jaN++); return el.dataset.ja; };
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();
  const text = (el) => clean(el ? (el.innerText || el.textContent) : "");
  const shown = (el) => {
    if (!el) return false;
    const s = win.getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const byIds = (ids) => clean((ids || "").split(/\s+/).map(id => text(doc.getElementById(id))).join(" "));
  const excluded = (el) => exclude && el.closest(exclude);
  const ownLabel = (el) => {
    const ll = el.getAttribute("aria-labelledby");
    if (ll) { const t = byIds(ll); if (t) return t; }
    if (el.labels && el.labels.length) { const t = clean(Array.from(el.labels).map(text).join(" ")); if (t) return t; }
    const al = el.getAttribute("aria-label");
    return al ? clean(al) : "";
  };
  const nearbyLabel = (el) => {
    let p = el.parentElement;
    for (let i = 0; i < 4 && p && p !== root.parentElement; i++, p = p.parentElement) {
      for (const c of p.querySelectorAll("label, legend, [class*='label' i], [class*='question' i]")) {
        if (c.contains(el) || c.querySelector("input, select, textarea")) continue;
        const t = text(c);
        if (t && t.length < 400) return t;
      }
    }
    return "";
  };
  const groupLabel = (el) => {
    const g = el.closest("fieldset, [role='radiogroup'], [role='group']");
    if (g) {
      const lg = g.querySelector("legend");
      if (lg && text(lg)) return text(lg);
      const ll = g.getAttribute("aria-labelledby");
      if (ll) { const t = byIds(ll); if (t) return t; }
      const al = g.getAttribute("aria-label");
      if (al) return clean(al);
    }
    let p = (g || el).parentElement;
    for (let i = 0; i < 4 && p && p !== root.parentElement; i++, p = p.parentElement) {
      for (const c of p.querySelectorAll("legend, label, span, p, h3, h4, div")) {
        if (c.contains(el) || c.querySelector("input, label")) continue;
        if (c.tagName === "LABEL" && c.htmlFor) continue;  // an option's own label
        if (c.children.length && c.querySelector("span, div, p")) continue;
        const t = text(c);
        if (t && t.length > 2 && t.length < 400) return t;
      }
    }
    return "";
  };
  const isReq = (el) => el.required || el.getAttribute("aria-required") === "true";
  const out = [], seen = new Set();
  for (const el of root.querySelectorAll("input, select, textarea")) {
    if (excluded(el) || el.disabled) continue;
    const type = (el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea" : (el.getAttribute("type") || "text")).toLowerCase();
    if (["hidden", "submit", "button", "image", "reset", "search"].includes(type)) continue;
    const invalid = el.getAttribute("aria-invalid") === "true";
    if (type === "file") {
      out.push({id: tag(el), kind: "file", label: ownLabel(el) || nearbyLabel(el), required: isReq(el),
                value: el.files && el.files.length ? el.files[0].name : "", meta: clean((el.id || "") + " " + (el.name || ""))});
      continue;
    }
    if (type === "radio" || type === "checkbox") {
      if (!shown(el) && !(el.labels && Array.from(el.labels).some(shown))) continue;
      if (type === "radio") {
        const key = "r:" + (el.name || tag(el));
        if (seen.has(key)) continue;
        seen.add(key);
        const radios = el.name
          ? Array.from(root.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`)).filter(r => !excluded(r))
          : [el];
        const checked = radios.find(r => r.checked);
        out.push({id: tag(radios[0]), kind: "radio", label: groupLabel(el), required: radios.some(isReq),
                  value: checked ? (ownLabel(checked) || checked.value) : "",
                  options: radios.map(r => ownLabel(r) || r.value), option_ids: radios.map(tag),
                  invalid: radios.some(r => r.getAttribute("aria-invalid") === "true")});
        continue;
      }
      const g = el.closest("fieldset, [role='group']");
      const boxes = g ? Array.from(g.querySelectorAll("input[type=checkbox]")).filter(b => !excluded(b)) : [el];
      if (boxes.length > 1) {
        const key = "c:" + tag(boxes[0]);
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({id: tag(boxes[0]), kind: "checkbox_group", label: groupLabel(el),
                  required: boxes.some(isReq) || (g && g.getAttribute("aria-required") === "true"),
                  value: boxes.filter(b => b.checked).map(b => ownLabel(b) || b.value).join("; "),
                  options: boxes.map(b => ownLabel(b) || b.value), option_ids: boxes.map(tag), invalid});
      } else {
        out.push({id: tag(el), kind: "checkbox", label: ownLabel(el) || nearbyLabel(el), required: isReq(el),
                  value: el.checked ? "Yes" : "No", invalid});
      }
      continue;
    }
    if (!shown(el) || el.readOnly) continue;
    if (type === "select") {
      const opts = Array.from(el.options);
      const real = opts.map((o, i) => ({t: clean(o.text), i, v: o.value}))
        .filter(o => o.t && o.v !== "" && !/^(select|choose|please select|--)/i.test(o.t));
      const current = real.find(o => o.i === el.selectedIndex);
      out.push({id: tag(el), kind: "select", label: ownLabel(el) || nearbyLabel(el), required: isReq(el),
                value: current ? current.t : "", options: real.map(o => o.t), option_index: real.map(o => o.i), invalid});
      continue;
    }
    let kind = type === "textarea" ? "textarea" : type;
    if (!["text", "textarea", "number", "email", "tel", "url", "date"].includes(kind)) kind = "text";
    if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list") kind = "combobox";
    out.push({id: tag(el), kind, label: ownLabel(el) || nearbyLabel(el) || el.placeholder || el.name || "",
              required: isReq(el), value: el.value || "", invalid});
  }
  for (const f of out) {
    const el = doc.querySelector(`[data-ja="${f.id}"]`);
    let p = el && el.parentElement, err = "";
    for (let i = 0; i < 3 && p && !err; i++, p = p.parentElement) {
      const e = p.querySelector("[role='alert'], [class*='error' i]:not(input):not(select):not(textarea)");
      if (e && shown(e) && text(e)) err = text(e).slice(0, 200);
    }
    f.error = err;
  }
  return out;
}
"""


def clean_label(label: str) -> str:
    label = re.sub(r"\s+", " ", label or "").strip()
    label = re.sub(r"\s*\*+\s*$", "", label)
    label = re.sub(r"\s*\((required|optional)\)\s*$", "", label, flags=re.I)
    label = re.sub(r"\s+required$", "", label, flags=re.I)
    half = len(label) // 2
    # Screen-reader text often repeats the label: "Foo bar Foo bar"
    if len(label) > 3 and len(label) % 2 == 1 and label[:half] == label[half + 1:]:
        label = label[:half]
    return label.strip()


@dataclass
class FormField:
    id: str
    kind: str
    label: str
    required: bool = False
    value: str = ""
    options: list[str] = field(default_factory=list)
    option_ids: list[str] = field(default_factory=list)
    option_index: list[int] = field(default_factory=list)
    invalid: bool = False
    error: str = ""
    meta: str = ""

    @classmethod
    def from_scan(cls, raw: dict[str, Any]) -> "FormField":
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        f = cls(**known)
        f.label = clean_label(f.label)
        f.options = [clean_label(o) for o in f.options]
        return f

    def prompt_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "label": self.label, "kind": self.kind, "required": self.required}
        if self.options:
            d["options"] = self.options
        return d


@dataclass
class Decision:
    value: str | list[str] | None
    source: str
    reason: str = ""


class FormFiller:
    def __init__(
        self,
        session: BrowserSession,
        store: Store,
        claude: Claude,
        settings: Settings,
        docs: Documents,
        console: Console,
        scan_exclude: str = "",
    ) -> None:
        self.session = session
        self.store = store
        self.claude = claude
        self.settings = settings
        self.docs = docs
        self.console = console
        self.scan_exclude = scan_exclude
        self.uploaded: set[str] = set()

    @property
    def page(self):
        return self.session.page

    # ---- scanning -------------------------------------------------------------

    def scan(self, container: Locator) -> list[FormField]:
        raw = container.evaluate(SCAN_JS, self.scan_exclude)
        return [FormField.from_scan(r) for r in raw]

    # ---- the main entry point -------------------------------------------------------

    def fill_page(
        self,
        container: Locator,
        job: Job,
        qa: list[dict[str, Any]],
        cover_letter: Path | None,
        resume_upload_ok: Callable[[Locator], bool] = lambda c: True,
        retry: bool = False,
    ) -> list[FormField]:
        """Fill every field on the current step. With retry=True (after the site
        rejected the step), fields flagged with an error are handed to the user."""
        fields = self.scan(container)
        decisions: dict[str, Decision] = {}
        for_claude: list[FormField] = []
        for_user: list[FormField] = []

        for f in fields:
            if f.kind == "file":
                self._handle_file(f, container, qa, cover_letter, resume_upload_ok)
                continue
            if retry and (f.invalid or f.error):
                for_user.append(f)
                continue
            if self._has_value(f):
                self._record(qa, f, f.value, "prefilled")
                continue
            decision = self._resolve_locally(f, cover_letter)
            if decision:
                decisions[f.id] = decision
            elif f.required or retry:
                for_claude.append(f)
            # Optional fields nobody has an answer for are left blank.

        if for_claude:
            answers = self._ask_claude(job, for_claude)
            for f in for_claude:
                answer = answers.get(f.id)
                value = self._from_claude(f, answer)
                if value is None or answer is None or answer.needs_human or answer.confidence == "low":
                    suggestion = value if value is not None else (answer.answer if answer else None)
                    decisions[f.id] = self._ask_user(f, suggestion, answer.reason if answer else None)
                else:
                    decisions[f.id] = Decision(value, "ai", answer.reason)

        for f in for_user:
            note = f" - site says: {f.error}" if f.error else ""
            decision = self._ask_user(f, f.value or None, f"the site rejected this answer{note}")
            decisions[f.id] = decision

        for f in fields:
            decision = decisions.get(f.id)
            if decision is None or decision.value in (None, "", []):
                continue
            if self._fill(f, decision.value):
                self._record(qa, f, decision.value, decision.source)
        return fields

    # ---- answer sources -----------------------------------------------------------

    def _has_value(self, f: FormField) -> bool:
        if f.kind == "checkbox":
            return False  # "No" is also a value; decide explicitly
        return bool(f.value and f.value.strip()) and not f.invalid

    def _resolve_locally(self, f: FormField, cover_letter: Path | None) -> Decision | None:
        if f.kind == "radio" and RESUME_RE.search(f.label):
            name = self.docs.cv_path.name.lower() if self.docs.cv_path else ""
            pick = next((o for o in f.options if name and name in o.lower()), None)
            return Decision(pick or f.options[0], "file") if f.options else None
        if f.kind == "textarea" and COVER_RE.search(f.label) and cover_letter:
            txt = cover_letter.with_suffix(".txt")
            if txt.exists():
                return Decision(txt.read_text(encoding="utf-8"), "cover letter")
        rule = rule_answer(f.label, f.kind, self.settings.profile)
        if rule is not None and (value := self._fit(f, rule)) is not None:
            return Decision(value, "profile")
        remembered = self.store.remembered_answer(f.label, f.kind)
        if remembered is not None and (value := self._fit(f, remembered)) is not None:
            return Decision(value, "memory")
        return None

    def _fit(self, f: FormField, answer: str | list[str]) -> str | list[str] | None:
        """Adapt an answer to the field type, or None if it doesn't fit."""
        if isinstance(answer, list):
            answer = "; ".join(answer)
        answer = str(answer).strip()
        if f.kind in CHOICE_KINDS:
            return match_option(answer, f.options)
        if f.kind == "checkbox_group":
            picks = [match_option(part, f.options) for part in answer.split(";") if part.strip()]
            return picks if picks and all(picks) else None
        if f.kind == "checkbox":
            lowered = answer.lower()
            if lowered in ("yes", "true", "y", "checked"):
                return "Yes"
            if lowered in ("no", "false", "n", "unchecked"):
                return "No"
            return None
        if f.kind == "number":
            digits = re.sub(r"[^\d.]", "", answer)
            return digits or None
        return answer or None

    def _ask_claude(self, job: Job, fields: list[FormField]) -> dict[str, FieldAnswer]:
        with self.console.status(f"Asking Claude about {len(fields)} question(s)..."):
            try:
                return self.claude.answer_fields(job, [f.prompt_dict() for f in fields])
            except (LLMError, anthropic.APIError) as exc:
                self.console.print(f"[yellow]Claude couldn't answer ({exc}); asking you instead.[/]")
                return {}

    def _from_claude(self, f: FormField, answer: FieldAnswer | None) -> str | list[str] | None:
        if answer is None:
            return None
        if f.kind in CHOICE_KINDS:
            chosen = answer.chosen_options[0] if answer.chosen_options else answer.answer
            return match_option(chosen, f.options)
        if f.kind == "checkbox_group":
            picks = [match_option(c, f.options) for c in answer.chosen_options]
            return picks if picks and all(picks) else None
        return self._fit(f, answer.answer)

    def _ask_user(self, f: FormField, suggestion: Any, reason: str | None) -> Decision:
        value = ask_field(self.console, f.label, f.kind, f.options, suggestion, reason, f.required)
        return Decision(value, "you")

    # ---- filling ---------------------------------------------------------------------

    def _loc(self, element_id: str) -> Locator:
        return self.page.locator(f'[data-ja="{element_id}"]')

    def _fill(self, f: FormField, value: str | list[str]) -> bool:
        try:
            if f.kind == "combobox":
                loc = self._loc(f.id)
                loc.fill("")
                loc.press_sequentially(str(value), delay=random.randint(50, 120))
                self.page.wait_for_timeout(1_500)
                option = self.page.locator("[role='option']:visible").first
                if option.count():
                    option.click()
            elif f.kind in TEXT_KINDS:
                self._loc(f.id).fill(str(value))
            elif f.kind == "select":
                idx = f.options.index(value)
                self._loc(f.id).select_option(index=f.option_index[idx])
            elif f.kind == "radio":
                idx = f.options.index(value)
                self._set_checked(self._loc(f.option_ids[idx]), True)
            elif f.kind == "checkbox":
                self._set_checked(self._loc(f.id), value == "Yes")
            elif f.kind == "checkbox_group":
                chosen = set(value if isinstance(value, list) else [value])
                for option, option_id in zip(f.options, f.option_ids):
                    self._set_checked(self._loc(option_id), option in chosen)
        except Exception as exc:
            self.console.print(f"[yellow]  Couldn't fill '{f.label}': {type(exc).__name__}: {str(exc)[:120]}[/]")
            return False
        self.session.short_pause()
        return True

    @staticmethod
    def _set_checked(loc: Locator, on: bool) -> None:
        attempts = (
            lambda: loc.set_checked(on, timeout=2_000),
            lambda: loc.set_checked(on, force=True, timeout=2_000),
            lambda: loc.evaluate("el => ((el.labels && el.labels[0]) || el).click()"),
        )
        for attempt in attempts:
            try:
                if loc.is_checked() == on:
                    return
                attempt()
                if loc.is_checked() == on:
                    return
            except Exception:
                continue

    def _handle_file(
        self,
        f: FormField,
        container: Locator,
        qa: list[dict[str, Any]],
        cover_letter: Path | None,
        resume_upload_ok: Callable[[Locator], bool],
    ) -> None:
        if f.value or f.id in self.uploaded:
            return
        hint = f"{f.label} {f.meta}"
        if COVER_RE.search(hint):
            path, what = cover_letter, "cover letter"
        elif RESUME_RE.search(hint) or not COVER_RE.search(hint) and f.required:
            if not resume_upload_ok(container):
                return
            path, what = (self.docs.cv_path if self.docs.cv_uploadable else None), "CV"
        else:
            return
        if not path or not path.exists():
            return
        try:
            self._loc(f.id).set_input_files(str(path))
        except Exception as exc:
            self.console.print(f"[yellow]  Couldn't upload {what}: {exc}[/]")
            return
        self.uploaded.add(f.id)
        self._record(qa, f, path.name, "file", label=f"Upload {what}")
        self.session.pause()

    # ---- bookkeeping --------------------------------------------------------------------

    @staticmethod
    def _record(qa: list[dict[str, Any]], f: FormField, value: Any, source: str, label: str | None = None) -> None:
        answer = "; ".join(value) if isinstance(value, list) else str(value)
        entry = {"id": f.id, "label": label or f.label, "kind": f.kind, "answer": answer,
                 "source": source, "options": f.options}
        for i, existing in enumerate(qa):
            if existing["id"] == f.id:
                qa[i] = entry
                return
        qa.append(entry)

    def refill(self, container: Locator, entry: dict[str, Any], new_value: Any, qa: list[dict[str, Any]]) -> bool:
        """Change one answer on the current step (used by the review screen's Edit)."""
        match = next((f for f in self.scan(container) if f.id == entry["id"]), None)
        if match is None:
            return False
        value = self._fit(match, new_value) if not isinstance(new_value, list) else new_value
        if value is None:
            value = new_value
        if self._fill(match, value):
            self._record(qa, match, value, "edited")
            return True
        return False

    def remember(self, qa: list[dict[str, Any]]) -> None:
        """Save the short answers you approved, so they're reused next time."""
        for entry in qa:
            if entry["source"] not in MEMORABLE_SOURCES or entry["kind"] not in MEMORABLE_KINDS:
                continue
            if len(entry["label"]) < 3 or len(entry["answer"]) > 100:
                continue
            self.store.remember_answer(entry["label"], entry["kind"], entry["answer"])
