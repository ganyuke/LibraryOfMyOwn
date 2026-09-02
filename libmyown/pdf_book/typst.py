from __future__ import annotations

import json


def typst_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _metadata_fields(metadata: dict[str, str]) -> tuple[str, str, str, str, str]:
    title = metadata.get("title") or metadata.get("name") or "Untitled"
    premise = metadata.get("premise", "")
    category = metadata.get("category", "")
    fandom = metadata.get("fandom", "")
    language = metadata.get("language", "")
    return title, premise, category, fandom, language


def _details_typ(category: str, fandom: str, language: str) -> str:
    details: list[str] = []
    for label, value in (("Category", category), ("Fandom", fandom), ("Language", language)):
        if value:
            details.append(
                f'[#text(weight: "bold")[{label}:] #text({typst_string(value)})]'
            )
    return " #h(0.8em) ".join(details)


def _first_page_block(title: str, premise: str, details_typ: str) -> str:
    premise_block = (
        f"#v(0.35em)\n#align(center)[#emph(text({typst_string(premise)}))]\n"
        if premise
        else ""
    )
    details_block = (
        f"#v(0.45em)\n#set text(size: 6.5pt)\n#align(center)[{details_typ}]\n"
        f"#set text(size: 8.5pt)\n"
        if details_typ
        else ""
    )
    return f"""// Compact title/metadata block on the first logical page.
#align(center)[
  #text(size: 14pt, weight: "bold")[#running-title]
]
{premise_block}{details_block}#v(0.65em)
"""


def _page_header_block() -> str:
    return """  header: context {
    set text(size: 6.5pt)
    let num = counter(page).display("1 / 1", both: true)
    if calc.odd(here().page()) {
      (emph(running-title), h(1fr), num).join()
    } else {
      (num, h(1fr), emph(running-title)).join()
    }
  },"""


def make_typst_document_print(body_typ: str, metadata: dict[str, str]) -> str:
    title, premise, category, fandom, language = _metadata_fields(metadata)
    details_typ = _details_typ(category, fandom, language)
    first_page = _first_page_block(title, premise, details_typ)

    return f"""// GENERATED FILE. Edit libmyown/pdf_book/typst.py, not this file.

#set terms(hanging-indent: 1.5em)
#set table(inset: 4pt, stroke: none)
#let horizontalrule = line(start: (15%, 0%), end: (85%, 0%), stroke: 0.45pt)

#let running-title = {typst_string(title)}

#set page(
  width: 4.25in,
  height: 5.5in,
  binding: left,
  margin: (
    inside: 0.43in,
    outside: 0.31in,
    top: 0.43in,
    bottom: 0.34in,
  ),
{_page_header_block()}
)

#set text(size: 8.5pt)
#set par(justify: true, leading: 0.42em)
#set heading(numbering: none)

{first_page}
// Pandoc-generated document body follows.
{body_typ}
"""


def make_typst_document_digital(body_typ: str, metadata: dict[str, str]) -> str:
    title, premise, category, fandom, language = _metadata_fields(metadata)
    details_typ = _details_typ(category, fandom, language)
    first_page = _first_page_block(title, premise, details_typ)

    return f"""// GENERATED FILE. Edit libmyown/pdf_book/typst.py, not this file.

#set terms(hanging-indent: 1.5em)
#set table(inset: 4pt, stroke: none)
#let horizontalrule = line(start: (15%, 0%), end: (85%, 0%), stroke: 0.45pt)

#let running-title = {typst_string(title)}

#set page(
  width: 4.25in,
  height: 5.5in,
  margin: (
    left: 0.37in,
    right: 0.37in,
    top: 0.43in,
    bottom: 0.34in,
  ),
{_page_header_block()}
)

#set text(size: 8.5pt)
#set par(justify: true, leading: 0.42em)
#set heading(numbering: none)

{first_page}
// Pandoc-generated document body follows.
{body_typ}
"""


def inject_typst_title_meta(
    book_typ: str,
    *,
    author: str = "",
    rev_label: str = "",
) -> str:
    if not author and not rev_label:
        return book_typ

    lines: list[str] = []
    if author:
        lines.append(
            f"#align(center)[#text(size: 7pt)[by #text({typst_string(author)})]]"
        )
    if rev_label:
        lines.append(
            f"#align(center)[#text(size: 6.5pt)[#text({typst_string(rev_label)})]]"
        )

    block = "#v(0.35em)\n" + "\n".join(lines) + "\n"
    anchor = '#text(size: 14pt, weight: "bold")[#running-title]\n]\n'
    if anchor in book_typ:
        return book_typ.replace(anchor, anchor + block, 1)
    return book_typ + block
