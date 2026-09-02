from __future__ import annotations

AUTHOR_MODE_DEFAULT = "default"
AUTHOR_MODE_EARLIEST = "earliest"


def display_author(identity: str, aliases: dict[str, str]) -> str:
    identity = identity.strip()
    if not identity:
        return ""
    alias = aliases.get(identity, "").strip()
    if alias:
        return alias
    return identity.split(" <", 1)[0].strip()


def format_author_identity(identity: str) -> str:
    identity = identity.strip()
    if " <" in identity and identity.endswith(">"):
        return identity
    return identity
