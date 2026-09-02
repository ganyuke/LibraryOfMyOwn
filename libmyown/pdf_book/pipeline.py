from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from libmyown.pdf_book.markdown import normalized_markdown
from libmyown.pdf_book.metadata import normalize_metadata
from libmyown.pdf_book.tools import check_external_tools, run_pandoc, run_typst
from libmyown.pdf_book.typst import (
    inject_typst_title_meta,
    make_typst_document_digital,
    make_typst_document_print,
)

Layout = Literal["print", "digital"]


def pdf_work_stem(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-") or "book"


def build_logical_pdf(
    work,
    *,
    layout: Layout,
    output_pdf: Path,
    work_dir: Path,
    blurb_fields: list[str] | None = None,
    author: str = "",
    rev_label: str = "",
) -> None:
    """Build a quarter-letter logical PDF in print or digital layout."""
    check_external_tools()
    work_dir.mkdir(parents=True, exist_ok=True)

    metadata = normalize_metadata(
        {"title": work.title, **work.fields},
        blurb_fields,
    )
    stem = pdf_work_stem(work.title)

    normalized_md = work_dir / f"{stem}.normalized.md"
    body_typ = work_dir / f"{stem}.body.typ"
    book_typ = work_dir / f"{stem}.book.typ"

    normalized_md.write_text(
        normalized_markdown(metadata, work.characters, work.body),
        encoding="utf-8",
    )
    run_pandoc(normalized_md, body_typ)

    typ_body_text = body_typ.read_text(encoding="utf-8")
    make_document = (
        make_typst_document_print if layout == "print" else make_typst_document_digital
    )
    book_text = inject_typst_title_meta(
        make_document(typ_body_text, metadata),
        author=author,
        rev_label=rev_label,
    )
    book_typ.write_text(book_text, encoding="utf-8")
    run_typst(book_typ, output_pdf)
