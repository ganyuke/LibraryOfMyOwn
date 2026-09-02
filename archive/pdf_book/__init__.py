"""Quarter-letter PDF book building (Pandoc + Typst + optional imposition)."""

from archive.pdf_book.impose import impose_cutstack, impose_onecut_fold
from archive.pdf_book.markdown import normalized_markdown
from archive.pdf_book.pipeline import build_logical_pdf
from archive.pdf_book.typst import make_typst_document_digital, make_typst_document_print

__all__ = [
    "build_logical_pdf",
    "impose_cutstack",
    "impose_onecut_fold",
    "make_typst_document_digital",
    "make_typst_document_print",
    "normalized_markdown",
]
