from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StoryContinuity:
    previous: list[str] = field(default_factory=list)
    next: list[str] = field(default_factory=list)


DEFAULT_FLAG_LABELS = {"wip": "WIP"}
DEFAULT_FLAG_COLORS = {"wip": "#8a6d00"}
DEFAULT_SITE_TITLE = "Library of My Own"
APP_LABEL = "Library of My Own"
HOME_LABEL = "Works"
SOURCE_REPO_URL = "https://github.com/ganyuke/LibraryOfMyOwn"


@dataclass
class FlagDef:
    label: str
    color: str = ""

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {"label": self.label}
        if self.color:
            payload["color"] = self.color
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> FlagDef:
        if isinstance(raw, str):
            return cls(label=raw, color=DEFAULT_FLAG_COLORS.get(raw, ""))
        if isinstance(raw, dict):
            label = str(raw.get("label", "")).strip()
            color = str(raw.get("color", "")).strip()
            return cls(label=label or "Flag", color=color)
        return cls(label="Flag")


@dataclass
class SiteConfig:
    public_url: str = ""
    site_title: str = DEFAULT_SITE_TITLE
    git_username: str = "git"
    stories_branch: str = ""
    show_login_link: bool = True
    expose_unpublished_continuity_titles: bool = False
    public_history: bool = True
    robots_noindex: bool = False
    published_paths: set[str] = field(default_factory=set)
    published_directories: set[str] = field(default_factory=set)
    suppressed_commits: dict[str, set[str]] = field(default_factory=dict)
    history_merges: dict[str, list[str]] = field(default_factory=dict)
    history_merge_meta: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    slug_redirects: dict[str, str] = field(default_factory=dict)
    flags: dict[str, FlagDef] = field(
        default_factory=lambda: {
            flag_id: FlagDef(label=label, color=DEFAULT_FLAG_COLORS.get(flag_id, ""))
            for flag_id, label in DEFAULT_FLAG_LABELS.items()
        }
    )
    work_flags: dict[str, list[str]] = field(default_factory=dict)
    story_continuity: dict[str, StoryContinuity] = field(default_factory=dict)
    blurb_fields: list[str] = field(default_factory=lambda: ["summary"])
    field_order: list[str] = field(default_factory=list)
    default_author: str = ""
    author_aliases: dict[str, str] = field(default_factory=dict)
    work_author_mode: dict[str, str] = field(default_factory=dict)
    work_author_override: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        continuity = {
            path: {
                "previous": links.previous,
                "next": links.next,
            }
            for path, links in sorted(self.story_continuity.items())
            if links.previous or links.next
        }
        payload: dict[str, Any] = {
            "public_url": self.public_url.strip(),
            "site_title": self.site_title.strip() or DEFAULT_SITE_TITLE,
            "git_username": self.git_username.strip() or "git",
            "stories_branch": self.stories_branch.strip(),
            "show_login_link": self.show_login_link,
            "expose_unpublished_continuity_titles": self.expose_unpublished_continuity_titles,
            "public_history": self.public_history,
            "robots_noindex": self.robots_noindex,
            "published_paths": sorted(self.published_paths),
            "published_directories": sorted(self.published_directories),
            "suppressed_commits": {
                path: sorted(shas) for path, shas in self.suppressed_commits.items()
            },
            "history_merges": {
                path: list(sources) for path, sources in sorted(self.history_merges.items())
            },
            "slug_redirects": dict(sorted(self.slug_redirects.items())),
            "flags": {
                flag_id: flag.to_dict() for flag_id, flag in sorted(self.flags.items())
            },
            "work_flags": {
                path: list(flags) for path, flags in sorted(self.work_flags.items()) if flags
            },
            "continuity": continuity,
            "blurb_fields": list(self.blurb_fields),
        }
        if self.field_order:
            payload["field_order"] = list(self.field_order)
        if self.default_author.strip():
            payload["default_author"] = self.default_author.strip()
        aliases = {
            identity: alias.strip()
            for identity, alias in sorted(self.author_aliases.items())
            if alias.strip()
        }
        if aliases:
            payload["author_aliases"] = aliases
        modes = {
            path: mode
            for path, mode in sorted(self.work_author_mode.items())
            if mode == "earliest"
        }
        if modes:
            payload["work_author_mode"] = modes
        overrides = {
            path: name.strip()
            for path, name in sorted(self.work_author_override.items())
            if name.strip()
        }
        if overrides:
            payload["work_author_override"] = overrides
        merge_meta = {
            dest: dict(sources)
            for dest, sources in sorted(self.history_merge_meta.items())
            if sources
        }
        if merge_meta:
            payload["history_merge_meta"] = merge_meta
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SiteConfig:
        continuity = data.get("continuity", {})
        if cls._is_graph_format(continuity):
            story_continuity = cls._migrate_graph_format(continuity)
        else:
            story_continuity = cls._parse_story_links(continuity)
        suppressed = {
            path: set(shas)
            for path, shas in data.get("suppressed_commits", {}).items()
        }
        history_merges = {
            path: list(sources)
            for path, sources in data.get("history_merges", {}).items()
        }
        slug_redirects = {
            str(old_slug): str(new_slug)
            for old_slug, new_slug in data.get("slug_redirects", {}).items()
        }
        history_merge_meta = {
            dest: dict(sources)
            for dest, sources in data.get("history_merge_meta", {}).items()
        }
        flag_labels = dict(data.get("flag_labels", DEFAULT_FLAG_LABELS))
        work_flags = {
            path: list(flags)
            for path, flags in data.get("work_flags", {}).items()
        }
        if "flags" in data:
            flags = {
                str(flag_id): FlagDef.from_dict(raw)
                for flag_id, raw in data["flags"].items()
            }
        else:
            flags = {
                flag_id: FlagDef(
                    label=label,
                    color=DEFAULT_FLAG_COLORS.get(flag_id, ""),
                )
                for flag_id, label in flag_labels.items()
            }
        for path in data.get("wip_paths", []):
            path_flags = work_flags.setdefault(path, [])
            if "wip" not in path_flags and "wip" in flags:
                path_flags.append("wip")
        return cls(
            public_url=str(data.get("public_url", data.get("origin", ""))).strip(),
            site_title=str(data.get("site_title", DEFAULT_SITE_TITLE)).strip()
            or DEFAULT_SITE_TITLE,
            git_username=str(data.get("git_username", "git")).strip() or "git",
            stories_branch=str(data.get("stories_branch", "")).strip(),
            show_login_link=bool(data.get("show_login_link", True)),
            expose_unpublished_continuity_titles=bool(
                data.get("expose_unpublished_continuity_titles", False)
            ),
            public_history=bool(data.get("public_history", True)),
            robots_noindex=bool(data.get("robots_noindex", False)),
            published_paths=set(data.get("published_paths", [])),
            published_directories=set(data.get("published_directories", [])),
            suppressed_commits=suppressed,
            history_merges=history_merges,
            history_merge_meta=history_merge_meta,
            slug_redirects=slug_redirects,
            flags=flags,
            work_flags=work_flags,
            story_continuity=story_continuity,
            blurb_fields=list(data.get("blurb_fields", ["summary"])),
            field_order=list(data.get("field_order", [])),
            default_author=str(data.get("default_author", "")).strip(),
            author_aliases={
                str(identity): str(alias).strip()
                for identity, alias in data.get("author_aliases", {}).items()
                if str(alias).strip()
            },
            work_author_mode={
                str(path): str(mode)
                for path, mode in data.get("work_author_mode", {}).items()
                if str(mode) == "earliest"
            },
            work_author_override={
                str(path): str(name).strip()
                for path, name in data.get("work_author_override", {}).items()
                if str(name).strip()
            },
        )

    @staticmethod
    def _is_graph_format(continuity: dict[str, Any]) -> bool:
        if "nodes" in continuity or "edges" in continuity:
            return True
        if "visible_paths" in continuity or "placeholders" in continuity:
            return True
        return False

    @classmethod
    def _parse_story_links(cls, continuity: dict[str, Any]) -> dict[str, StoryContinuity]:
        links: dict[str, StoryContinuity] = {}
        for path, raw in continuity.items():
            if not isinstance(raw, dict):
                continue
            previous = [str(item) for item in raw.get("previous", []) if item]
            next_paths = [str(item) for item in raw.get("next", []) if item]
            if previous or next_paths:
                links[path] = StoryContinuity(previous=previous, next=next_paths)
        return links

    @classmethod
    def _migrate_graph_format(cls, continuity: dict[str, Any]) -> dict[str, StoryContinuity]:
        id_map: dict[str, str] = {}
        for node in continuity.get("nodes", []):
            if node.get("type") == "work" and node.get("path"):
                id_map[node["id"]] = node["path"]

        buckets: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: {"previous": [], "next": []}
        )
        for edge in continuity.get("edges", []):
            from_id = id_map.get(edge["from"], edge["from"])
            to_id = id_map.get(edge["to"], edge["to"])
            if "/" not in from_id or "/" not in to_id:
                continue
            if to_id not in buckets[from_id]["next"]:
                buckets[from_id]["next"].append(to_id)
            if from_id not in buckets[to_id]["previous"]:
                buckets[to_id]["previous"].append(from_id)

        links: dict[str, StoryContinuity] = {}
        for path, raw in buckets.items():
            previous = raw["previous"]
            next_paths = raw["next"]
            if previous or next_paths:
                links[path] = StoryContinuity(previous=previous, next=next_paths)
        return links


def normalize_flag_id(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def default_site_config() -> SiteConfig:
    return SiteConfig()


def load_site_config(path: Path) -> SiteConfig:
    if not path.is_file():
        return default_site_config()
    data = json.loads(path.read_text(encoding="utf-8"))
    return SiteConfig.from_dict(data)


def save_site_config(path: Path, config: SiteConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
