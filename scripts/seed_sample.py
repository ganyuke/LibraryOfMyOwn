#!/usr/bin/env python3
"""Seed the stories repo from sample markdown fixtures."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from dulwich import porcelain

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from archive.site_config import SiteConfig, save_site_config

LONG_DRAFT = "Series/The Long Draft.md"


def _stories_source() -> Path:
    default = ROOT / "fixtures" / "sample-stories"
    return Path(os.environ.get("STORIES_SOURCE", str(default))).expanduser().resolve()


def _collect_markdown(source: Path) -> list[Path]:
    return sorted(source.rglob("*.md"))


def _commit_author() -> bytes:
    return b"LibraryOfMyOwn Seed <writer@local>"


def _commit(repo, message: str, paths: list[str] | None = None) -> None:
    if paths:
        porcelain.add(repo, paths=paths)
    porcelain.commit(
        repo,
        message=message.encode(),
        author=_commit_author(),
        committer=_commit_author(),
        sign=False,
    )


def _edit_long_draft(worktree: Path, editor: Callable[[str], str | None]) -> list[str]:
    path = worktree / LONG_DRAFT
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    updated = editor(text)
    if updated is None or updated == text:
        return []
    path.write_text(updated, encoding="utf-8")
    return [LONG_DRAFT]


def _revision_opening(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "The workshop smelled of oil and sawdust before Mara even turned the key.",
            "The workshop smelled of oil, sawdust, and yesterday's coffee before Mara even turned the key.",
            1,
        ),
    )


def _revision_baker(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "He paid without haggling and left a cinnamon roll on the counter as interest.",
            "He paid without haggling, thanked her twice, and left a cinnamon roll on the counter as interest.",
            1,
        ),
    )


def _revision_eli_intro(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            'Eli appeared at noon, as he often did, with two cups and a question.',
            'Eli appeared at noon, as he often did, with two cups, a folded newspaper, and a question.',
            1,
        ),
    )


def _revision_rain(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "The sound made the workshop feel smaller and safer.",
            "The sound made the workshop feel smaller, safer, and briefly like a ship cabin.",
            1,
        ),
    )


def _revision_leaving_talk(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            '"I think about it the way you think about jumping into cold water. Interesting idea. Bad timing."',
            '"I think about it the way you think about jumping into cold water. Interesting idea. Terrible timing."',
            1,
        ).replace(
            '"So not today."',
            '"So not today. Maybe not this year."',
            1,
        ),
    )


def _revision_teenager(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "A teenager with headphones around her neck and a wristwatch full of glitter glue.",
            "A teenager with headphones around her neck and a wristwatch full of glitter glue that had somehow jammed the balance wheel.",
            1,
        ),
    )


def _revision_evening_walk(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "Mara walked him to the corner because the streetlamp there had been out for a week",
            "Mara walked him to the corner because the streetlamp there had been out for a week and the cooper's yard echoed",
            1,
        ),
    )


def _revision_attic_note(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "The last item said: *ask Eli about the attic*.",
            "The last item said: *ask Eli about the attic before I chicken out*.",
            1,
        ),
    )


def _revision_night_ending(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "Some problems could be solved. Others only had to be carried.",
            "Some problems could be solved with patience and brass. Others only had to be carried until they grew lighter.",
            1,
        ),
    )


def _revision_second_day(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            '"I\'m a person with small screwdrivers."',
            '"I\'m a person with small screwdrivers and strong opinions about springs."',
            1,
        ),
    )


def _revision_summary(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "summary: >\n  A long demo fic for testing scroll, revision history, and word-level diffs\n  across many commits.",
            "summary: >\n  A long demo fic for scroll testing, revision history, and word-level diffs\n  across many commits.",
            1,
        ),
    )


def _revision_final_lines(worktree: Path) -> list[str]:
    return _edit_long_draft(
        worktree,
        lambda t: t.replace(
            "The long draft of her life kept ticking forward, one honest second at a time.",
            "The long draft of her life kept ticking forward—one honest second at a time, whether she was ready or not.",
            1,
        ),
    )


def _revision_sample_two(worktree: Path) -> list[str]:
    path = worktree / "Series/Sample Two.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "picked a safer route",
        "picked a safer route, watching for loose stones",
        1,
    )
    path.write_text(text, encoding="utf-8")
    return ["Series/Sample Two.md"]


REVISIONS: list[tuple[str, Callable[[Path], list[str]]]] = [
    ("Expand opening smell and atmosphere", _revision_opening),
    ("Flesh out baker payment beat", _revision_baker),
    ("Add detail to Eli's entrance", _revision_eli_intro),
    ("Extend rain-at-the-window passage", _revision_rain),
    ("Revise leaving-city conversation", _revision_leaving_talk),
    ("Clarify teenager watch repair", _revision_teenager),
    ("Expand evening walk with Eli", _revision_evening_walk),
    ("Sharpen attic note on tomorrow's list", _revision_attic_note),
    ("Rework late-night reflection", _revision_night_ending),
    ("Add humor to second-day baker scene", _revision_second_day),
    ("Update summary in frontmatter", _revision_summary),
    ("Polish closing line", _revision_final_lines),
    ("Tweak Sample Two river path", _revision_sample_two),
]


def main() -> None:
    source = _stories_source()
    if not source.is_dir():
        raise SystemExit(f"Stories source not found: {source}")

    stories = _collect_markdown(source)
    if not stories:
        raise SystemExit(f"No .md files under {source}")

    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    repo_path = data_dir / "stories.git"
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    if repo_path.exists():
        shutil.rmtree(repo_path)
    porcelain.init(str(repo_path), bare=True)

    revision_count = 0
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "worktree"
        porcelain.init(str(worktree))
        added: list[str] = []
        for src in stories:
            rel = src.relative_to(source)
            dest = worktree / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            added.append(str(rel).replace("\\", "/"))

        repo = porcelain.open_repo(str(worktree))
        _commit(repo, f"Import {len(added)} sample stories", added)

        for message, apply_revision in REVISIONS:
            changed = apply_revision(worktree)
            if changed:
                _commit(repo, message, changed)
                revision_count += 1

        porcelain.push(
            repo,
            f"file://{repo_path.resolve()}",
            refspecs=["refs/heads/master:refs/heads/master"],
        )

    site_path = data_dir / "site.json"
    site = SiteConfig()
    site.published_directories = {"Series"}
    save_site_config(site_path, site)

    print(f"Seeded {repo_path} with {len(added)} files from {source}")
    print(f"Added {revision_count} revision commits (mostly on The Long Draft)")
    print(f"Published Series/ in {site_path}")


if __name__ == "__main__":
    main()
