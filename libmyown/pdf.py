from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from libmyown.content import parse_work

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


class PdfBusy(Exception):
    """Too many PDF builds are already running."""


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
            logger.warning("skipping PDF script %s", path, exc_info=True)
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
    return f"libmyown_pdf_script_{safe_dir}_{script_id}"


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
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
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


def run_build_job(job: dict) -> None:
    """Entry point inside the child process (see libmyown.pdf_runner)."""
    scripts_dir = Path(job["scripts_dir"])
    module = _load_script_module(scripts_dir, job["script_id"])
    build = getattr(module, "build", None)
    if not callable(build):
        raise ValueError(f"PDF script {job['script_id']} has no build() function")
    work_data = job["work"]
    work = PdfWork(
        title=work_data["title"],
        fields=dict(work_data["fields"]),
        characters=[tuple(item) for item in work_data["characters"]],
        body=work_data["body"],
    )
    _call_build(
        build,
        Path(job["input_md"]),
        Path(job["output_pdf"]),
        Path(job["work_dir"]),
        work=work,
        author=job["author"],
        rev_label=job["rev_label"],
        blurb_fields=list(job["blurb_fields"]),
        work_path=job["work_path"],
        title=work.title,
    )


class PdfService:
    """Builds PDFs in a child process with a hard timeout and a concurrency cap.

    Finished PDFs are cached by (commit, path, script). Builds happen in a private
    temp directory and are moved into place atomically, so a reader never sees a
    partial file, and concurrent requests for the same PDF share one build.
    """

    def __init__(
        self,
        scripts_dir: Path | None,
        cache_dir: Path,
        *,
        max_cache_bytes: int = 512 * 1024 * 1024,
        timeout_seconds: float = 180.0,
        max_concurrent: int = 1,
        queue_wait_seconds: float = 30.0,
    ) -> None:
        self.scripts_dir = scripts_dir
        self.cache_dir = cache_dir
        self.max_cache_bytes = max_cache_bytes
        self.timeout_seconds = timeout_seconds
        self.queue_wait_seconds = queue_wait_seconds
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_guard = threading.Lock()
        self._options_lock = threading.Lock()
        self._options_signature: tuple | None = None
        self._options: list[PdfOption] = []

    # -- options -------------------------------------------------------------

    def _scripts_signature(self) -> tuple | None:
        if self.scripts_dir is None or not self.scripts_dir.is_dir():
            return None
        return tuple(
            sorted((path.name, path.stat().st_mtime_ns) for path in self.scripts_dir.glob("*.py"))
        )

    def options(self) -> list[PdfOption]:
        signature = self._scripts_signature()
        if signature == self._options_signature:
            return self._options
        with self._options_lock:
            if signature != self._options_signature:
                self._options = discover_pdf_options(self.scripts_dir) if signature else []
                self._options_signature = signature
            return self._options

    def option_ids(self) -> set[str]:
        return {option.id for option in self.options()}

    # -- building ------------------------------------------------------------

    def _key_lock(self, key: str) -> threading.Lock:
        with self._key_locks_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def get(
        self,
        *,
        markdown_text: str,
        script_id: str,
        commit_sha: str,
        path: str,
        author: str = "",
        rev_label: str = "",
        blurb_fields: list[str] | None = None,
    ) -> Path:
        if self.scripts_dir is None or script_id not in self.option_ids():
            raise FileNotFoundError(script_id)
        module = _load_script_module(self.scripts_dir, script_id)
        out_dir = self.cache_dir / commit_sha / path.replace("/", "_") / script_id
        output_pdf = out_dir / _output_filename(path, module, script_id)
        key = str(output_pdf)
        with self._key_lock(key):
            if output_pdf.is_file():
                _touch(output_pdf)
                return output_pdf
            if not self._slots.acquire(timeout=self.queue_wait_seconds):
                raise PdfBusy()
            try:
                self._build(
                    markdown_text=markdown_text,
                    script_id=script_id,
                    path=path,
                    output_pdf=output_pdf,
                    author=author,
                    rev_label=rev_label,
                    blurb_fields=blurb_fields or ["summary"],
                )
            finally:
                self._slots.release()
        with self._key_locks_guard:
            self._key_locks.pop(key, None)
        self._prune(keep=output_pdf)
        return output_pdf

    def _build(
        self,
        *,
        markdown_text: str,
        script_id: str,
        path: str,
        output_pdf: Path,
        author: str,
        rev_label: str,
        blurb_fields: list[str],
    ) -> None:
        tmp_root = self.cache_dir / ".tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="build-", dir=tmp_root))
        try:
            parsed = parse_work(markdown_text, fallback_title=Path(path).stem.replace("-", " "))
            input_md = work_dir / "source.md"
            input_md.write_text(markdown_text, encoding="utf-8")
            tmp_pdf = work_dir / output_pdf.name
            job = {
                "scripts_dir": str(self.scripts_dir),
                "script_id": script_id,
                "input_md": str(input_md),
                "output_pdf": str(tmp_pdf),
                "work_dir": str(work_dir),
                "work": asdict(
                    PdfWork(
                        title=parsed.title,
                        fields=dict(parsed.fields),
                        characters=list(parsed.characters),
                        body=parsed.body,
                    )
                ),
                "author": author,
                "rev_label": rev_label,
                "blurb_fields": blurb_fields,
                "work_path": path,
            }
            job_file = work_dir / "job.json"
            job_file.write_text(json.dumps(job), encoding="utf-8")
            self._run_child(job_file, work_dir)
            if not tmp_pdf.is_file() or tmp_pdf.stat().st_size == 0:
                raise FileNotFoundError(f"PDF not produced: {output_pdf.name}")
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_pdf, output_pdf)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _run_child(self, job_file: Path, work_dir: Path) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(_PROJECT_ROOT), env.get("PYTHONPATH", "")])
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "libmyown.pdf_runner", str(job_file)],
            cwd=work_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            output, _ = proc.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.communicate()
            raise TimeoutError(f"PDF build exceeded {self.timeout_seconds:.0f}s")
        if proc.returncode != 0:
            tail = output.decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"PDF build failed (exit {proc.returncode}):\n{tail}")

    # -- cache size --------------------------------------------------------------

    def _prune(self, *, keep: Path) -> None:
        if self.max_cache_bytes <= 0:
            return
        files: list[tuple[float, int, Path]] = []
        total = 0
        for pdf in self.cache_dir.rglob("*.pdf"):
            if ".tmp" in pdf.relative_to(self.cache_dir).parts:
                continue
            try:
                stat = pdf.stat()
            except FileNotFoundError:
                continue
            files.append((stat.st_mtime, stat.st_size, pdf))
            total += stat.st_size
        if total <= self.max_cache_bytes:
            return
        for _mtime, size, pdf in sorted(files):
            if total <= self.max_cache_bytes:
                break
            if pdf == keep:
                continue
            try:
                pdf.unlink()
                total -= size
            except FileNotFoundError:
                continue
            _remove_empty_parents(pdf.parent, stop=self.cache_dir)


def _touch(path: Path) -> None:
    try:
        os.utime(path)
    except OSError:
        pass


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _remove_empty_parents(directory: Path, *, stop: Path) -> None:
    while directory != stop and stop in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent
