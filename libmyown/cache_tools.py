from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from libmyown.git_repo import StoriesRepo
from libmyown.site_config import clear_site_config_cache
from libmyown.work_index import WorkIndexStore


@dataclass(frozen=True)
class PdfCacheStats:
    entries: int
    bytes_on_disk: int


def pdf_cache_stats(cache_dir: Path) -> PdfCacheStats:
    if not cache_dir.is_dir():
        return PdfCacheStats(entries=0, bytes_on_disk=0)
    entries = 0
    total_bytes = 0
    for child in cache_dir.iterdir():
        entries += 1
        if child.is_file():
            total_bytes += child.stat().st_size
        elif child.is_dir():
            for path in child.rglob("*"):
                if path.is_file():
                    total_bytes += path.stat().st_size
    return PdfCacheStats(entries=entries, bytes_on_disk=total_bytes)


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
