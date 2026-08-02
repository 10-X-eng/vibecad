# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detect removed architectural objects without mutating an opened document."""

from __future__ import annotations

from typing import Any


_LEGACY_PROXY_TYPES = frozenset(
    {
        "ArchSectionView",
        "AxisSystem",
        "Building",
        "BuildingPart",
        "CurtainWall",
        "Equipment",
        "Floor",
        "Frame",
        "IfcAnnotation",
        "IfcBuildingStorey",
        "Panel",
        "PanelCut",
        "PanelSheet",
        "Pipe",
        "Project",
        "Rebar",
        "Roof",
        "Schedule",
        "SectionPlane",
        "Site",
        "Space",
        "Stairs",
        "Structure",
        "Wall",
        "Window",
        "WorkingPlaneProxy",
    }
)


def _proxy_identity(obj: Any) -> tuple[str, str, str]:
    proxy = getattr(obj, "Proxy", None)
    if proxy is None:
        return "", "", ""
    proxy_type = str(getattr(proxy, "Type", "") or "")
    proxy_class = type(proxy)
    return (
        proxy_type,
        str(getattr(proxy_class, "__name__", "") or ""),
        str(getattr(proxy_class, "__module__", "") or ""),
    )


def is_legacy_architecture_object(obj: Any) -> bool:
    """Return whether an object carries a signature from the removed toolset."""

    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id == "TechDraw::DrawViewArch" or type_id.startswith(("Arch::", "BIM::")):
        return True

    properties = set(getattr(obj, "PropertiesList", ()) or ())
    if properties.intersection({"IfcData", "IfcRole", "IfcType"}):
        return True

    proxy_type, proxy_name, proxy_module = _proxy_identity(obj)
    if proxy_type in _LEGACY_PROXY_TYPES:
        return True
    identity = f"{proxy_module}.{proxy_name}".lower()
    return identity.startswith(("arch", "bim", "nativeifc")) or ".arch" in identity


def find_legacy_architecture_objects(document: Any) -> list[Any]:
    """Return legacy objects from a document, preserving document order."""

    return [
        obj
        for obj in list(getattr(document, "Objects", ()) or ())
        if is_legacy_architecture_object(obj)
    ]


def warning_text(count: int) -> str:
    noun = "object" if count == 1 else "objects"
    return (
        "Architectural and BIM support has been removed from VibeCAD. "
        f"This document contains {count} legacy architectural {noun}; they are unsupported "
        "and may be missing or degraded. To preserve or convert them, close this document "
        "without saving, open it in a VibeCAD/FreeCAD release that still includes BIM, and "
        "export the required geometry to a neutral format such as STEP, BREP, or mesh."
    )
