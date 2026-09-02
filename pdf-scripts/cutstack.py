"""PDF builder: letter cut-and-stack imposition."""

from __future__ import annotations

from pathlib import Path

from libmyown.pdf import PdfWork
from libmyown.pdf_book.impose import impose_cutstack
from libmyown.pdf_book.pipeline import build_logical_pdf, pdf_work_stem

label = "Letter, cut-and-stack"
order = 1
suffix = "-print-letter-cutstack.pdf"


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
        raise ValueError("cutstack PDF builder requires parsed work metadata")
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_work_stem(work.title)
    logical_pdf = work_dir / f"{stem}-logical-quarter-letter.pdf"
    build_logical_pdf(
        work,
        layout="print",
        output_pdf=logical_pdf,
        work_dir=work_dir,
        blurb_fields=blurb_fields,
        author=author,
        rev_label=rev_label,
    )
    impose_cutstack(logical_pdf, output_pdf)
