from __future__ import annotations

AUTHOR_MODE_DEFAULT = "default"
AUTHOR_MODE_EARLIEST = "earliest"

EXCEPTION_TYPE_LABELS = {
    AUTHOR_MODE_DEFAULT: "Default author",
    AUTHOR_MODE_EARLIEST: "Earliest commit",
    "custom": "Custom",
}


def work_exception_type(
    path: str,
    work_author_mode: dict[str, str],
    work_author_override: dict[str, str],
) -> str | None:
    if path in work_author_override:
        return "custom"
    mode = work_author_mode.get(path)
    if mode in (AUTHOR_MODE_DEFAULT, AUTHOR_MODE_EARLIEST):
        return mode
    return None


def list_work_exceptions(
    work_author_mode: dict[str, str],
    work_author_override: dict[str, str],
) -> list[dict[str, str]]:
    paths = sorted(
        set(work_author_mode) | set(work_author_override),
        key=str.lower,
    )
    exceptions: list[dict[str, str]] = []
    for path in paths:
        exception_type = work_exception_type(
            path,
            work_author_mode,
            work_author_override,
        )
        if exception_type is None:
            continue
        exceptions.append(
            {
                "path": path,
                "type": exception_type,
                "type_label": EXCEPTION_TYPE_LABELS[exception_type],
                "override": work_author_override.get(path, ""),
            }
        )
    return exceptions


def is_redundant_exception(exception_type: str, site_rule: str) -> bool:
    if exception_type == "custom":
        return False
    return exception_type == site_rule


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
