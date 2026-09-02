from __future__ import annotations

import copy
import math
import sys
from pathlib import Path


def _check_pdfimpose() -> None:
    try:
        import pdfimpose  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "pdfimpose is required for cut-and-stack output. "
            f"Install it with: {sys.executable} -m pip install pdfimpose"
        ) from exc


def _check_pypdf() -> None:
    try:
        import pypdf  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is required for one-cut/fold output. "
            f"Install it with: {sys.executable} -m pip install pypdf"
        ) from exc


def impose_cutstack(logical_pdf: Path, output_pdf: Path) -> None:
    """4-up cut-and-stack: two center cuts, four piles, then edge-bind."""
    _check_pdfimpose()
    from pdfimpose.schema.wire import impose as wire_impose

    wire_impose(
        [logical_pdf],
        output_pdf,
        signature=(2, 2),
        imargin=0,
        omargin=0,
        mark=[],
    )


def impose_onecut_fold(logical_pdf: Path, output_pdf: Path) -> dict[str, int | bool]:
    """
    Create a 4-up US Letter saddle-stitch imposition optimized for one horizontal cut.
    """
    _check_pypdf()
    from pypdf import PdfReader, PdfWriter, Transformation

    reader = PdfReader(str(logical_pdf))
    page_count = len(reader.pages)
    if page_count == 0:
        raise RuntimeError("logical PDF contains no pages")

    first = reader.pages[0]
    pw = float(first.mediabox.width)
    ph = float(first.mediabox.height)

    expected_pw = 4.25 * 72.0
    expected_ph = 5.5 * 72.0
    if abs(pw - expected_pw) > 1.0 or abs(ph - expected_ph) > 1.0:
        raise RuntimeError(
            "one-cut/fold expects 4.25 x 5.5 inch logical pages; "
            f"got approximately {pw / 72:.3f} x {ph / 72:.3f} inches"
        )

    padded_pages = int(math.ceil(page_count / 4.0) * 4)
    booklet_sheet_count = padded_pages // 4
    letter_sheet_count = int(math.ceil(booklet_sheet_count / 2.0))

    def logical_page(number: int):
        if 1 <= number <= page_count:
            return reader.pages[number - 1]
        return None

    def booklet_spread(sheet_index: int) -> tuple[tuple[int, int], tuple[int, int]]:
        k = sheet_index
        front = (padded_pages - 2 * k, 1 + 2 * k)
        back = (2 + 2 * k, padded_pages - 1 - 2 * k)
        return front, back

    def merge_logical(dest, page_number: int, x: float, y: float) -> None:
        src = logical_page(page_number)
        if src is None:
            return
        page = copy.deepcopy(src)
        llx = float(page.mediabox.left)
        lly = float(page.mediabox.bottom)
        transform = Transformation().translate(tx=x - llx, ty=y - lly)
        dest.merge_transformed_page(page, transform, expand=False)

    writer = PdfWriter()
    letter_w = pw * 2.0
    letter_h = ph * 2.0

    for letter_index in range(letter_sheet_count):
        top_index = letter_index
        bottom_index = letter_index + letter_sheet_count

        front_out = writer.add_blank_page(width=letter_w, height=letter_h)
        back_out = writer.add_blank_page(width=letter_w, height=letter_h)

        for booklet_index, y in ((top_index, ph), (bottom_index, 0.0)):
            if booklet_index >= booklet_sheet_count:
                continue

            front_pair, back_pair = booklet_spread(booklet_index)

            merge_logical(front_out, front_pair[0], 0.0, y)
            merge_logical(front_out, front_pair[1], pw, y)
            merge_logical(back_out, back_pair[0], 0.0, y)
            merge_logical(back_out, back_pair[1], pw, y)

    writer.add_metadata({
        "/Title": "One-cut fold imposition",
        "/Subject": "US Letter 4-up, one horizontal cut, fold/nest saddle-stitch",
    })
    with output_pdf.open("wb") as f:
        writer.write(f)

    return {
        "logical_pages": page_count,
        "padded_pages": padded_pages,
        "booklet_sheets": booklet_sheet_count,
        "letter_sheets": letter_sheet_count,
        "discard_blank_half": booklet_sheet_count % 2 == 1,
    }
