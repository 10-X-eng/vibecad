# SPDX-License-Identifier: LGPL-2.1-or-later

"""List material cards from the FreeCAD material library."""

from __future__ import annotations

from typing import Any


MAX_MATERIALS_RETURNED = 50


TOOL_SPEC = {
    "name": "material.list_materials",
    "description": (
        "List material cards from the FreeCAD material library with their "
        "exact UUIDs. Filter by name to find a specific material (for example "
        "'steel' or 'abs'), then use the returned UUID in the active "
        "authoring surface's material assignment. Results are capped at "
        f"{MAX_MATERIALS_RETURNED}; narrow the filter if truncated."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "MaterialWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": (
                    "Case-insensitive, punctuation-insensitive word search "
                    "against material name, directory path, description, "
                    "and tags. Every word must match, so 'aluminum 6061' "
                    "finds 'Aluminum-6061-T6' ('aluminium' and 'fibre' "
                    "spellings also work). Empty string lists all materials "
                    "up to the cap."
                ),
            },
        },
        "required": ["name_filter"],
        "additionalProperties": False,
    },
}


def run(service: Any, name_filter: str) -> dict[str, Any]:
    try:
        import Materials
    except ImportError:
        return _invalid(
            "The Materials module is not available in this FreeCAD build; "
            "the material library cannot be read."
        )
    try:
        manager = Materials.MaterialManager()
        all_materials = dict(manager.Materials)
    except Exception as exc:
        return _invalid(f"Could not read the material library: {exc}")

    tokens = _normalize(str(name_filter or "")).split()
    matched: list[dict[str, Any]] = []
    total_matched = 0
    for uuid, material in sorted(
        all_materials.items(),
        key=lambda item: str(getattr(item[1], "Name", "")),
    ):
        if not _matches(material, tokens):
            continue
        total_matched += 1
        if len(matched) >= MAX_MATERIALS_RETURNED:
            continue
        record: dict[str, Any] = {
            "name": str(getattr(material, "Name", "")),
            "uuid": str(uuid),
            "library": str(getattr(material, "LibraryName", "")),
            "directory": str(getattr(material, "Directory", "")),
        }
        tags = [str(tag) for tag in (getattr(material, "Tags", None) or [])]
        if tags:
            record["tags"] = tags
        matched.append(record)

    result: dict[str, Any] = {
        "ok": True,
        "material_count": total_matched,
        "materials": matched,
    }
    if total_matched > MAX_MATERIALS_RETURNED:
        result["truncated"] = True
        result["note"] = (
            f"{total_matched} materials matched; only the first "
            f"{MAX_MATERIALS_RETURNED} are returned. Narrow name_filter."
        )
    if total_matched == 0:
        result["note"] = (
            "No materials matched the filter. Try fewer or shorter words, "
            "for example 'steel' instead of 'stainless steel 316L'."
        )
    return result


_SPELLING_FOLDS = (
    ("aluminium", "aluminum"),
    ("fibre", "fiber"),
)


def _normalize(text: str) -> str:
    """Lowercase, fold British spellings to American, punctuation to spaces."""
    lowered = text.lower()
    for british, american in _SPELLING_FOLDS:
        lowered = lowered.replace(british, american)
    cleaned = "".join(ch if ch.isalnum() else " " for ch in lowered)
    return " ".join(cleaned.split())


def _matches(material: Any, tokens: list[str]) -> bool:
    if not tokens:
        return True
    haystacks = [
        str(getattr(material, "Name", "")),
        str(getattr(material, "Directory", "")),
        str(getattr(material, "Description", "")),
    ]
    haystacks.extend(str(tag) for tag in (getattr(material, "Tags", None) or []))
    haystack = _normalize(" ".join(haystacks))
    return all(token in haystack for token in tokens)


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
