"""PDF builder: quarter-letter reading order (print gutter margins)."""

from __future__ import annotations

from pathlib import Path

from libmyown.pdf import PdfWork
from libmyown.pdf_book.pipeline import build_logical_pdf

label = "Quarter-letter, reading order"
order = 0
suffix = "-logical-quarter-letter.pdf"


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
        raise ValueError("logical PDF builder requires parsed work metadata")
    build_logical_pdf(
        work,
        layout="print",
        output_pdf=output_pdf,
        work_dir=work_dir,
        blurb_fields=blurb_fields,
        author=author,
        rev_label=rev_label,
    )
