from __future__ import annotations

import json


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def escape_pandoc_scene_breaks(body: str) -> str:
    """Screenplays use --- between scenes; Pandoc treats that as YAML metadata."""
    return "\n".join("***" if line.strip() == "---" else line for line in body.splitlines())


def normalized_markdown(
    metadata: dict[str, str],
    characters: list[tuple[str, str]],
    body: str,
) -> str:
    """Build valid Pandoc Markdown without changing the original source file."""
    out: list[str] = ["---"]

    preferred = ["title", "premise", "category", "fandom", "language"]
    seen: set[str] = set()
    for key in preferred:
        if key in metadata:
            out.append(f"{key}: {yaml_quote(metadata[key])}")
            seen.add(key)

    for key, value in metadata.items():
        if key not in seen and key != "characters":
            out.append(f"{key}: {yaml_quote(value)}")

    if characters:
        out.append("characters:")
        for name, desc in characters:
            out.append(f"  - name: {yaml_quote(name)}")
            out.append(f"    description: {yaml_quote(desc)}")

    out.extend(["---", "", escape_pandoc_scene_breaks(body).rstrip(), ""])

    if characters:
        out.extend([
            "```{=typst}",
            "#pagebreak()",
            "```",
            "",
            "# Character Guide",
            "",
        ])
        for name, desc in characters:
            out.append(f"**{name}**  ")
            if desc:
                out.append(desc)
            out.append("")

    return "\n".join(out).rstrip() + "\n"
