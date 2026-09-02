from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from archive.content import parse_work


@dataclass(frozen=True)
class PdfOption:
    id: str
    label: str


@dataclass(frozen=True)
class PdfWork:
    title: str
    fields: dict[str, str]
    characters: list[tuple[str, str]]
    body: str


def _safe_stem(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "book"


def _output_filename(path: str, module, script_id: str) -> str:
    suffix = getattr(module, "suffix", None)
    if isinstance(suffix, str) and suffix:
        base = suffix if suffix.startswith("-") or suffix.startswith(".") else f"-{suffix}"
        if not base.endswith(".pdf"):
            base = f"{base}.pdf"
        return f"{_safe_stem(path)}{base}"
    return f"{_safe_stem(path)}-{script_id}.pdf"


def discover_pdf_options(pdf_scripts_dir: Path | None) -> list[PdfOption]:
    if pdf_scripts_dir is None or not pdf_scripts_dir.is_dir():
        return []

    discovered: list[tuple[int, str, PdfOption]] = []
    for path in pdf_scripts_dir.glob("*.py"):
        if path.name.startswith("_"):
            continue
        script_id = path.stem
        try:
            module = _load_script_module(pdf_scripts_dir, script_id)
        except Exception:
            continue
        label = getattr(module, "label", None)
        build = getattr(module, "build", None)
        if not isinstance(label, str) or not callable(build):
            continue
        order = getattr(module, "order", 0)
        if not isinstance(order, int):
            order = 0
        discovered.append((order, label.lower(), PdfOption(id=script_id, label=label)))

    discovered.sort(key=lambda item: (item[0], item[1]))
    return [opt for _, _, opt in discovered]


def _module_name(pdf_scripts_dir: Path, script_id: str) -> str:
    safe_dir = str(pdf_scripts_dir.resolve()).replace("/", "_").replace("\\", "_")
    return f"archive_pdf_script_{safe_dir}_{script_id}"


def _load_script_module(pdf_scripts_dir: Path, script_id: str):
    script_path = pdf_scripts_dir / f"{script_id}.py"
    if not script_path.is_file():
        raise FileNotFoundError(f"PDF script not found: {script_path}")

    module_name = _module_name(pdf_scripts_dir, script_id)
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load PDF script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _call_build(
    build,
    input_md: Path,
    output_pdf: Path,
    work_dir: Path,
    *,
    work: PdfWork,
    author: str,
    rev_label: str,
    blurb_fields: list[str],
    work_path: str,
    title: str,
) -> None:
    params = inspect.signature(build).parameters
    kwargs: dict[str, object] = {}
    if "work" in params:
        kwargs["work"] = work
    if "author" in params:
        kwargs["author"] = author
    if "rev_label" in params:
        kwargs["rev_label"] = rev_label
    if "blurb_fields" in params:
        kwargs["blurb_fields"] = blurb_fields
    if "work_path" in params:
        kwargs["work_path"] = work_path
    if "title" in params:
        kwargs["title"] = title
    build(input_md, output_pdf, work_dir, **kwargs)


def generate_pdf(
    *,
    markdown_text: str,
    script_id: str,
    pdf_scripts_dir: Path,
    cache_dir: Path,
    commit_sha: str,
    path: str,
    author: str = "",
    rev_label: str = "",
    blurb_fields: list[str] | None = None,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"{commit_sha}/{path.replace('/', '_')}"
    out_dir = cache_dir / cache_key / script_id
    out_dir.mkdir(parents=True, exist_ok=True)

    module = _load_script_module(pdf_scripts_dir, script_id)
    build = getattr(module, "build", None)
    if not callable(build):
        raise ValueError(f"PDF script {script_id} has no build() function")

    output_pdf = out_dir / _output_filename(path, module, script_id)
    if output_pdf.is_file():
        return output_pdf

    fallback_title = Path(path).stem.replace("-", " ")
    parsed = parse_work(markdown_text, fallback_title=fallback_title)
    work = PdfWork(
        title=parsed.title,
        fields=dict(parsed.fields),
        characters=list(parsed.characters),
        body=parsed.body,
    )

    input_md = out_dir / "source.md"
    input_md.write_text(markdown_text, encoding="utf-8")
    _call_build(
        build,
        input_md,
        output_pdf,
        out_dir,
        work=work,
        author=author,
        rev_label=rev_label,
        blurb_fields=blurb_fields or ["summary"],
        work_path=path,
        title=work.title,
    )

    if not output_pdf.is_file():
        raise FileNotFoundError(f"PDF not produced: {output_pdf}")
    return output_pdf
