"""Save cover letters as PDF (for uploading) and TXT (for pasting)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fpdf import FPDF

from .config import COVER_LETTER_DIR, Settings
from .store import Job

WINDOWS_FONT = Path("C:/Windows/Fonts/arial.ttf")
ASCII_MAP = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " "})


def safe_filename(text: str, limit: int = 40) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")
    return text[:limit] or "untitled"


def letter_paths(job: Job) -> tuple[Path, Path]:
    stem = f"{safe_filename(job.company, 30)}_{safe_filename(job.title)}_{job.id[:6]}"
    return COVER_LETTER_DIR / f"{stem}.pdf", COVER_LETTER_DIR / f"{stem}.txt"


def save_cover_letter(job: Job, text: str, settings: Settings) -> Path:
    pdf_path, txt_path = letter_paths(job)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(text, encoding="utf-8")

    personal = settings.profile.get("personal") or {}
    name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
    contact = " | ".join(
        str(v)
        for v in (
            personal.get("email"),
            personal.get("phone"),
            ", ".join(x for x in (personal.get("city"), personal.get("state")) if x),
            personal.get("linkedin_url"),
        )
        if v
    )

    pdf = FPDF(format="Letter")
    pdf.set_margins(25, 22, 25)
    pdf.set_auto_page_break(True, margin=22)
    pdf.add_page()
    if WINDOWS_FONT.exists():
        pdf.add_font("Body", "", str(WINDOWS_FONT))
        family, clean = "Body", (lambda s: s)
    else:  # built-in fonts are Latin-1 only
        family = "Helvetica"
        clean = lambda s: s.translate(ASCII_MAP).encode("latin-1", "replace").decode("latin-1")  # noqa: E731

    if name:
        pdf.set_font(family, size=15)
        pdf.cell(0, 8, clean(name), new_x="LMARGIN", new_y="NEXT")
    if contact:
        pdf.set_font(family, size=9.5)
        pdf.cell(0, 6, clean(contact), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font(family, size=11)
    pdf.cell(0, 6, date.today().strftime("%B %d, %Y").replace(" 0", " "), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        pdf.multi_cell(0, 5.6, clean(paragraph.strip()), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    pdf.output(str(pdf_path))
    return pdf_path
