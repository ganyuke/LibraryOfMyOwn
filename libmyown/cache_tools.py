from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from libmyown.git_repo import StoriesRepo
from libmyown.site_config import clear_site_config_cache
from libmyown.work_index import WorkIndexStore


@dataclass(frozen=True)
class PdfCacheStats:
    pdf_count: int
    bytes_on_disk: int


def pdf_cache_stats(cache_dir: Path) -> PdfCacheStats:
    if not cache_dir.is_dir():
        return PdfCacheStats(pdf_count=0, bytes_on_disk=0)
    pdf_count = 0
    total_bytes = 0
    for path in cache_dir.rglob("*"):
        if not path.is_file():
            continue
        total_bytes += path.stat().st_size
        if path.suffix.lower() == ".pdf":
            pdf_count += 1
    return PdfCacheStats(pdf_count=pdf_count, bytes_on_disk=total_bytes)


def clear_pdf_cache(cache_dir: Path) -> int:
    if not cache_dir.is_dir():
        return 0
    removed = 0
    for child in cache_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    return removed


def clear_runtime_caches(
    repo: StoriesRepo,
    work_index: WorkIndexStore | None = None,
) -> None:
    repo.invalidate()
    clear_site_config_cache()
    if work_index is not None:
        work_index.invalidate()


def format_bytes(count: int) -> str:
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KB"
    return f"{count / (1024 * 1024):.1f} MB"
