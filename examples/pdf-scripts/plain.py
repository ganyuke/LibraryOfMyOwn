"""Example PDF builder: plain Pandoc markdown → PDF.

Copy this file into your PDF_SCRIPTS directory and ensure pandoc is on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

label = "PDF"
suffix = ".pdf"


def _with_meta_header(text: str, *, author: str, rev_label: str) -> str:
    if not author and not rev_label:
        return text

    parts: list[str] = []
    if author:
        parts.append(f"**{author}**")
    if rev_label:
        parts.append(f"*{rev_label}*")
    header = " · ".join(parts)

    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body = "\n".join(lines[i + 1 :]).lstrip("\n")
                if text.endswith("\n"):
                    body += "\n"
                return "\n".join(lines[: i + 1]) + f"\n\n{header}\n\n{body}"
    return f"{header}\n\n{text}"


def build(
    input_md: Path,
    output_pdf: Path,
    work_dir: Path,
    *,
    author: str = "",
    rev_label: str = "",
    title: str = "",
    work_path: str = "",
) -> None:
    del work_dir
    if shutil.which("pandoc") is None:
        raise RuntimeError("pandoc is required for the plain PDF builder")
    source = _with_meta_header(
        input_md.read_text(encoding="utf-8"),
        author=author,
        rev_label=rev_label,
    )
    cmd = ["pandoc", "-", "-o", str(output_pdf)]
    if title:
        cmd = ["pandoc", "-M", f"title={title}", "-", "-o", str(output_pdf)]
    subprocess.run(
        cmd,
        input=source,
        text=True,
        check=True,
    )
