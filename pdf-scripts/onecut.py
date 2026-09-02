"""PDF builder: letter one-cut fold imposition."""

from __future__ import annotations

from pathlib import Path

from archive.pdf import PdfWork
from archive.pdf_book.impose import impose_onecut_fold
from archive.pdf_book.pipeline import build_logical_pdf, pdf_work_stem

label = "Letter, one-cut fold"
order = 2
suffix = "-print-letter-onecut-fold.pdf"


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
        raise ValueError("onecut PDF builder requires parsed work metadata")
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
    impose_onecut_fold(logical_pdf, output_pdf)
