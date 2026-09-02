from __future__ import annotations


def normalize_metadata(
    metadata: dict[str, str],
    blurb_fields: list[str] | None = None,
) -> dict[str, str]:
    """Map the configured blurb field to the premise key expected by Typst templates."""
    metadata = dict(metadata)
    for name in blurb_fields or ["summary"]:
        key = name.strip().lower().replace("-", "_").replace(" ", "_")
        if metadata.get(key):
            metadata["premise"] = metadata[key]
            break
    return metadata
