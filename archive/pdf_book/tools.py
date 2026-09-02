from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def check_external_tools(*, require_typst: bool = True) -> None:
    if shutil.which("pandoc") is None:
        raise RuntimeError("pandoc is not installed or not on PATH")
    if require_typst and shutil.which("typst") is None:
        raise RuntimeError("typst is not installed or not on PATH")


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def run_pandoc(normalized_md: Path, body_typ: Path) -> None:
    run(
        [
            "pandoc",
            str(normalized_md),
            "--from=markdown",
            "--to=typst",
            "-o",
            str(body_typ),
        ]
    )


def run_typst(book_typ: Path, output_pdf: Path) -> None:
    run(["typst", "compile", str(book_typ), str(output_pdf)])
