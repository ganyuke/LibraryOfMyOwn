from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import markdown
import yaml

BULLET_RE = re.compile(r"^\s*[-*•–—]\s*(.+?)\s*$")
KEY_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _-]*?)\s*:\s*(.*?)\s*$")
AO3_HOST = "archiveofourown.org"


@dataclass(frozen=True)
class WorkMeta:
    title: str
    fields: dict[str, str]
    characters: list[tuple[str, str]]
    body: str
    body_html: str
    word_count: int

    def blurb(self, blurb_fields: list[str]) -> str:
        return work_blurb(self.fields, blurb_fields)

    def display_rows(
        self,
        blurb_fields: list[str],
        field_order: list[str] | None = None,
    ) -> list[tuple[str, str]]:
        return work_display_rows(
            self.fields,
            blurb_fields=blurb_fields,
            field_order=field_order,
        )


def normalize_field_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def humanize_field_key(key: str) -> str:
    return key.replace("_", " ").strip().title()


def resolve_blurb_key(fields: dict[str, str], blurb_fields: list[str]) -> str | None:
    for name in blurb_fields:
        key = normalize_field_key(name)
        if fields.get(key):
            return key
    return None


def work_blurb(fields: dict[str, str], blurb_fields: list[str]) -> str:
    key = resolve_blurb_key(fields, blurb_fields)
    return fields[key] if key else ""


def _ao3_work_url(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    path = value.removeprefix("/")
    if re.fullmatch(r"\d+", path):
        return f"https://{AO3_HOST}/works/{path}"
    if re.fullmatch(r"\d+/chapters/\d+", path):
        return f"https://{AO3_HOST}/works/{path}"
    return None


def resolve_crosspost_url(label: str, raw: str) -> str | None:
    """Resolve a crosspost target. AO3 accepts work ids; everything else needs a URL."""
    value = raw.strip()
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    if "ao3" in label.strip().lower():
        return _ao3_work_url(value)
    return None


def work_display_rows(
    fields: dict[str, str],
    *,
    blurb_fields: list[str],
    field_order: list[str] | None = None,
) -> list[tuple[str, str]]:
    blurb_key = resolve_blurb_key(fields, blurb_fields)
    remaining = {key: value for key, value in fields.items() if key != blurb_key and value}

    if field_order:
        ordered_keys: list[str] = []
        for name in field_order:
            key = normalize_field_key(name)
            if key in remaining:
                ordered_keys.append(key)
        for key in sorted(remaining):
            if key not in ordered_keys:
                ordered_keys.append(key)
    else:
        ordered_keys = sorted(remaining.keys())

    return [(humanize_field_key(key), remaining[key]) for key in ordered_keys]


def _finalize_body(body: str, *, source_had_trailing_newline: bool) -> str:
    body = body.lstrip("\n")
    if source_had_trailing_newline:
        body += "\n"
    return body


def _body_after_delimiter(lines: list[str], delimiter_index: int, *, trailing_newline: bool) -> str:
    return _finalize_body(
        "\n".join(lines[delimiter_index + 1 :]),
        source_had_trailing_newline=trailing_newline,
    )


def _split_implicit_frontmatter(lines: list[str], *, trailing_newline: bool) -> tuple[list[str], str] | None:
    if not lines or not KEY_RE.match(lines[0]):
        return None

    fm_lines: list[str] = []
    in_characters = False
    for index, line in enumerate(lines):
        if not line.strip():
            if fm_lines:
                return fm_lines, _body_after_delimiter(
                    lines, index, trailing_newline=trailing_newline
                )
            continue

        key_match = KEY_RE.match(line)
        if key_match and not line.lstrip().startswith(("-", "*", "•", "–", "—")):
            in_characters = normalize_field_key(key_match.group(1)) == "characters"
            fm_lines.append(line)
            continue

        if in_characters and (
            BULLET_RE.match(line) or line.startswith((" ", "\t"))
        ):
            fm_lines.append(line)
            continue

        if fm_lines:
            body = "\n".join(lines[index:])
            return fm_lines, _finalize_body(body, source_had_trailing_newline=trailing_newline)
        return None

    return None


def split_frontmatter(text: str) -> tuple[list[str], str]:
    lines = text.splitlines()
    if not lines:
        return [], text
    trailing_newline = text.endswith("\n")

    if lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return lines[1:index], _body_after_delimiter(
                    lines, index, trailing_newline=trailing_newline
                )
        return [], text

    for index, line in enumerate(lines):
        if line.strip() == "---" and index > 0:
            return lines[:index], _body_after_delimiter(
                lines, index, trailing_newline=trailing_newline
            )

    implicit = _split_implicit_frontmatter(lines, trailing_newline=trailing_newline)
    if implicit is not None:
        return implicit

    return [], text


def _yaml_metadata_looks_corrupted(metadata: dict[str, str]) -> bool:
    for key in metadata:
        if key.startswith(("–", "-", "•", "–_", "-_")):
            return True
    return False


def _parse_frontmatter_lines(
    lines: list[str],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    try:
        yaml_meta, yaml_chars = parse_yaml_frontmatter(fm_lines := lines)
        human_meta, human_chars = parse_human_frontmatter(fm_lines)
    except yaml.YAMLError:
        return parse_human_frontmatter(lines)

    if human_chars and not yaml_chars:
        return human_meta, human_chars
    if yaml_chars:
        return yaml_meta, yaml_chars
    if human_meta and (not yaml_meta or _yaml_metadata_looks_corrupted(yaml_meta)):
        return human_meta, human_chars
    return yaml_meta or human_meta, yaml_chars or human_chars


def parse_human_frontmatter(lines: list[str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    metadata: dict[str, str] = {}
    characters: list[tuple[str, str]] = []
    current_key: str | None = None

    for raw in lines:
        if not raw.strip():
            continue
        key_match = KEY_RE.match(raw)
        if key_match and not raw.lstrip().startswith(("-", "*", "•", "–", "—")):
            key_raw, value = key_match.groups()
            key = normalize_field_key(key_raw)
            current_key = key
            if key != "characters":
                metadata[key] = value.strip()
            continue
        if current_key == "characters":
            bullet = BULLET_RE.match(raw)
            item = bullet.group(1) if bullet else raw.strip()
            if not item:
                continue
            if ":" in item:
                name, desc = item.split(":", 1)
                characters.append((name.strip(), desc.strip()))
            else:
                characters.append((item.strip(), ""))
        elif current_key and current_key in metadata:
            metadata[current_key] = (metadata[current_key] + " " + raw.strip()).strip()
    return metadata, characters


def parse_yaml_frontmatter(lines: list[str]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    yaml_text = "\n".join(lines)
    data = yaml.safe_load(yaml_text) or {}
    if not isinstance(data, dict):
        return {}, []

    characters: list[tuple[str, str]] = []
    raw_chars = None
    for key in list(data.keys()):
        if str(key).lower() == "characters":
            raw_chars = data.pop(key)
            break

    if isinstance(raw_chars, list):
        for item in raw_chars:
            if isinstance(item, dict):
                if "name" in item:
                    characters.append(
                        (str(item.get("name", "")), str(item.get("description", "")))
                    )
                elif len(item) == 1:
                    name, desc = next(iter(item.items()))
                    characters.append((str(name), str(desc)))
                else:
                    for name, desc in item.items():
                        characters.append((str(name), str(desc)))
            elif isinstance(item, str):
                if ":" in item:
                    name, desc = item.split(":", 1)
                    characters.append((name.strip(), desc.strip()))
                else:
                    characters.append((item.strip(), ""))

    metadata: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, (list, dict)):
            continue
        normalized = normalize_field_key(str(key))
        if value is not None and str(value).strip():
            metadata[normalized] = str(value).strip()
    return metadata, characters


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def render_markdown(body: str) -> str:
    return markdown.markdown(
        body,
        extensions=["smarty", "sane_lists", "nl2br"],
        output_format="html5",
    )


@dataclass(frozen=True)
class WorkSummary:
    title: str
    word_count: int


def parse_work_summary(text: str, *, fallback_title: str) -> WorkSummary:
    fm_lines, body = split_frontmatter(text)
    if fm_lines:
        metadata, _characters = _parse_frontmatter_lines(fm_lines)
    else:
        metadata = {}
    title = metadata.get("title", "") or fallback_title
    return WorkSummary(title=title, word_count=count_words(body))


def parse_work(text: str, *, fallback_title: str) -> WorkMeta:
    fm_lines, body = split_frontmatter(text)
    metadata: dict[str, str]
    characters: list[tuple[str, str]]
    if fm_lines:
        metadata, characters = _parse_frontmatter_lines(fm_lines)
    else:
        metadata, characters = {}, []

    title = metadata.pop("title", "") or fallback_title
    fields = {key: value for key, value in metadata.items() if value}

    return WorkMeta(
        title=title,
        fields=fields,
        characters=characters,
        body=body,
        body_html=render_markdown(body),
        word_count=count_words(body),
    )


def format_datetime(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def format_rev_date(dt) -> str:
    return f"{dt.day} {dt.strftime('%b %Y')}"


def format_date_reader(dt) -> str:
    return f"{dt.day} {dt.strftime('%B %Y')}"


def format_words(count: int) -> str:
    return f"{count:,}"


def revision_tooltip(short_sha: str, committed_at: str) -> str:
    return f"Revision {short_sha} · {committed_at}"


def parse_field_name_list(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def discover_work_field_keys(repo) -> list[str]:
    from pathlib import Path

    keys: set[str] = set()
    for path in repo.list_markdown_paths():
        text = repo.get_blob_text(path)
        if not text:
            continue
        meta = parse_work(text, fallback_title=Path(path).stem)
        keys.update(meta.fields.keys())
    return sorted(keys)
