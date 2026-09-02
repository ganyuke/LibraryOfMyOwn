from __future__ import annotations

import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class ByteDiffStats:
    added: int
    removed: int

    @property
    def changed(self) -> bool:
        return self.added > 0 or self.removed > 0


def diff_byte_stats(old_text: str, new_text: str) -> ByteDiffStats:
    matcher = SequenceMatcher(None, old_text, new_text)
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            removed += len(old_text[i1:i2].encode("utf-8"))
        elif tag == "insert":
            added += len(new_text[j1:j2].encode("utf-8"))
        elif tag == "replace":
            removed += len(old_text[i1:i2].encode("utf-8"))
            added += len(new_text[j1:j2].encode("utf-8"))
    return ByteDiffStats(added=added, removed=removed)


def format_compare_byte_summary(stats: ByteDiffStats) -> str:
    if not stats.changed:
        return ""
    parts: list[str] = []
    if stats.added:
        parts.append(f'<span class="compare-bytes-add">+{stats.added}</span>')
    if stats.removed:
        parts.append(f'<span class="compare-bytes-del">−{stats.removed}</span>')
    return f"({' '.join(parts)})"


def _tokenize_line(text: str) -> list[str]:
    return re.findall(r"\S+|\s+", text)


def _word_diff_line(old_line: str, new_line: str) -> str:
    old_tokens = _tokenize_line(old_line)
    new_tokens = _tokenize_line(new_line)
    matcher = SequenceMatcher(None, old_tokens, new_tokens)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append(html.escape("".join(old_tokens[i1:i2])))
        elif tag == "delete":
            parts.append(
                f'<del class="diff-del">{html.escape("".join(old_tokens[i1:i2]))}</del>'
            )
        elif tag == "insert":
            parts.append(
                f'<ins class="diff-ins">{html.escape("".join(new_tokens[j1:j2]))}</ins>'
            )
        elif tag == "replace":
            parts.append(
                f'<del class="diff-del">{html.escape("".join(old_tokens[i1:i2]))}</del>'
            )
            parts.append(
                f'<ins class="diff-ins">{html.escape("".join(new_tokens[j1:j2]))}</ins>'
            )
    return "".join(parts)


def immersive_diff_html(old_text: str, new_text: str) -> str:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = SequenceMatcher(None, old_lines, new_lines)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in old_lines[i1:i2]:
                parts.append(html.escape(line))
                parts.append("\n")
        elif tag == "delete":
            for line in old_lines[i1:i2]:
                parts.append(f'<del class="diff-del">{html.escape(line)}</del>\n')
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                parts.append(f'<ins class="diff-ins">{html.escape(line)}</ins>\n')
        elif tag == "replace":
            old_block = old_lines[i1:i2]
            new_block = new_lines[j1:j2]
            if len(old_block) == len(new_block) == 1:
                parts.append(_word_diff_line(old_block[0], new_block[0]))
                parts.append("\n")
            else:
                for line in old_block:
                    parts.append(f'<del class="diff-del">{html.escape(line)}</del>\n')
                for line in new_block:
                    parts.append(f'<ins class="diff-ins">{html.escape(line)}</ins>\n')
    return "".join(parts)


def format_byte_change_html(stats: ByteDiffStats) -> str:
    return format_compare_byte_summary(stats)


def _char_diff_pair(old_line: str, new_line: str) -> tuple[str, str]:
    matcher = SequenceMatcher(None, old_line, new_line)
    old_parts: list[str] = []
    new_parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            old_parts.append(html.escape(old_line[i1:i2]))
            new_parts.append(html.escape(new_line[j1:j2]))
        elif tag == "delete":
            old_parts.append(
                f'<span class="diffchange">{html.escape(old_line[i1:i2])}</span>'
            )
        elif tag == "insert":
            new_parts.append(
                f'<span class="diffchange">{html.escape(new_line[j1:j2])}</span>'
            )
        elif tag == "replace":
            old_parts.append(
                f'<span class="diffchange">{html.escape(old_line[i1:i2])}</span>'
            )
            new_parts.append(
                f'<span class="diffchange">{html.escape(new_line[j1:j2])}</span>'
            )
    return "".join(old_parts), "".join(new_parts)


def _line_cell(text: str, *, line_class: str) -> str:
    return f'<td class="{line_class}"><div>{text or "&#160;"}</div></td>'


def _split_row(
    *,
    left_marker: str,
    left_html: str,
    left_class: str,
    right_marker: str,
    right_html: str,
    right_class: str,
) -> str:
    return (
        "<tr>"
        f'<td class="diff-marker">{left_marker}</td>'
        f"{_line_cell(left_html, line_class=left_class)}"
        f'<td class="diff-marker">{right_marker}</td>'
        f"{_line_cell(right_html, line_class=right_class)}"
        "</tr>"
    )


def _unified_row(marker: str, content_html: str, *, line_class: str) -> str:
    return (
        "<tr>"
        f'<td class="diff-marker">{marker}</td>'
        f"{_line_cell(content_html, line_class=line_class)}"
        "</tr>"
    )


def _append_replace_rows(
    rows: list[tuple[str, str | None, str | None]],
    old_block: list[str],
    new_block: list[str],
) -> None:
    inner = SequenceMatcher(None, old_block, new_block)
    for tag, i1, i2, j1, j2 in inner.get_opcodes():
        if tag == "equal":
            for line in old_block[i1:i2]:
                rows.append(("context", line, line))
        elif tag == "delete":
            for line in old_block[i1:i2]:
                rows.append(("deleted", line, None))
        elif tag == "insert":
            for line in new_block[j1:j2]:
                rows.append(("added", None, line))
        elif tag == "replace":
            old_lines = old_block[i1:i2]
            new_lines = new_block[j1:j2]
            pair_count = min(len(old_lines), len(new_lines))
            for index in range(pair_count):
                rows.append(("changed", old_lines[index], new_lines[index]))
            for line in old_lines[pair_count:]:
                rows.append(("deleted", line, None))
            for line in new_lines[pair_count:]:
                rows.append(("added", None, line))


def _build_diff_rows(old_text: str, new_text: str) -> list[tuple[str, str | None, str | None]]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    rows: list[tuple[str, str | None, str | None]] = []
    matcher = SequenceMatcher(None, old_lines, new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in old_lines[i1:i2]:
                rows.append(("context", line, line))
        elif tag == "delete":
            for line in old_lines[i1:i2]:
                rows.append(("deleted", line, None))
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                rows.append(("added", None, line))
        elif tag == "replace":
            _append_replace_rows(rows, old_lines[i1:i2], new_lines[j1:j2])
    return rows


def _has_changes(rows: list[tuple[str, str | None, str | None]]) -> bool:
    return any(kind != "context" for kind, _, _ in rows)


def _render_split_row(kind: str, old_line: str | None, new_line: str | None) -> str:
    if kind == "context":
        escaped = html.escape(old_line or "")
        return _split_row(
            left_marker="",
            left_html=escaped,
            left_class="diff-context",
            right_marker="",
            right_html=escaped,
            right_class="diff-context",
        )
    if kind == "deleted":
        return _split_row(
            left_marker="−",
            left_html=html.escape(old_line or ""),
            left_class="diff-deletedline",
            right_marker="",
            right_html="",
            right_class="diff-empty",
        )
    if kind == "added":
        return _split_row(
            left_marker="",
            left_html="",
            left_class="diff-empty",
            right_marker="+",
            right_html=html.escape(new_line or ""),
            right_class="diff-addedline",
        )
    old_html, new_html = _char_diff_pair(old_line or "", new_line or "")
    return _split_row(
        left_marker="−",
        left_html=old_html,
        left_class="diff-deletedline",
        right_marker="+",
        right_html=new_html,
        right_class="diff-addedline",
    )


def _render_unified_row(kind: str, old_line: str | None, new_line: str | None) -> str:
    if kind == "context":
        return _unified_row("", html.escape(old_line or ""), line_class="diff-context")
    if kind == "deleted":
        return _unified_row("−", html.escape(old_line or ""), line_class="diff-deletedline")
    if kind == "added":
        return _unified_row("+", html.escape(new_line or ""), line_class="diff-addedline")
    old_html, new_html = _char_diff_pair(old_line or "", new_line or "")
    return _unified_row("−", old_html, line_class="diff-deletedline") + _unified_row(
        "+", new_html, line_class="diff-addedline"
    )


def split_diff_html(old_text: str, new_text: str) -> str:
    rows = _build_diff_rows(old_text, new_text)
    if not _has_changes(rows):
        return ""
    body = "".join(_render_split_row(kind, old_line, new_line) for kind, old_line, new_line in rows)
    return (
        '<table class="diff diff-split" role="presentation">'
        "<colgroup>"
        '<col class="diff-marker-col">'
        '<col class="diff-content-col">'
        '<col class="diff-marker-col">'
        '<col class="diff-content-col">'
        "</colgroup>"
        "<tbody>"
        f"{body}"
        "</tbody>"
        "</table>"
    )


def unified_diff_html(old_text: str, new_text: str) -> str:
    rows = _build_diff_rows(old_text, new_text)
    if not _has_changes(rows):
        return ""
    body = "".join(
        _render_unified_row(kind, old_line, new_line) for kind, old_line, new_line in rows
    )
    return (
        '<table class="diff diff-unified" role="presentation">'
        "<tbody>"
        f"{body}"
        "</tbody>"
        "</table>"
    )


def diff_html(old_text: str, new_text: str, *, view: str = "immersive") -> str:
    if old_text == new_text:
        return ""
    if view == "unified":
        return unified_diff_html(old_text, new_text)
    if view == "split":
        return split_diff_html(old_text, new_text)
    return immersive_diff_html(old_text, new_text)


def word_diff_html(old_text: str, new_text: str) -> str:
    return immersive_diff_html(old_text, new_text)
