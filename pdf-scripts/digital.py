"""PDF builder: digital quarter-letter (symmetric margins, screen reading)."""

from __future__ import annotations

from pathlib import Path

from archive.pdf import PdfWork
from archive.pdf_book.pipeline import build_logical_pdf

label = "Digital (quarter-letter)"
order = -1
suffix = "-digital-quarter-letter.pdf"


def build(
    input_md: Path,
    output_pdf: Path,
    work_dir: Path,
    *,
    work: PdfWork | None = None,
    author: str = "",
    rev_label: str = "",
    blurb_fields: list[str] | None = None,
    work_path: str = "",
    title: str = "",
) -> None:
    del input_md, work_path, title
    if work is None:
        raise ValueError("digital PDF builder requires parsed work metadata")
    build_logical_pdf(
        work,
        layout="digital",
        output_pdf=output_pdf,
        work_dir=work_dir,
        blurb_fields=blurb_fields,
        author=author,
        rev_label=rev_label,
    )
