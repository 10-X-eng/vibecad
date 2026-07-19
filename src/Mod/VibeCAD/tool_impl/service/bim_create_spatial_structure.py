# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create the BIM spatial skeleton: one site, one building, named levels."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "bim.create_spatial_structure",
    "description": (
        "Create the native BIM spatial skeleton in one call: a Site containing "
        "a Building containing one level (building storey) per entry, each "
        "placed at its exact elevation. Create this structure before walls so "
        "elements can be assigned to levels; add elements to a level via the "
        "level_object parameter of bim.create_wall and bim.create_structure."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "BIMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "site_label": {
                "type": "string",
                "description": "Visible label for the new site, e.g. 'MainSite'.",
            },
            "building_label": {
                "type": "string",
                "description": (
                    "Visible label for the new building, e.g. 'BuildingA'."
                ),
            },
            "levels": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "description": (
                    "Ordered levels (building storeys) to create inside the "
                    "building, lowest first."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": (
                                "Visible label for this level, e.g. 'GroundFloor'."
                            ),
                        },
                        "elevation_mm": {
                            "type": "number",
                            "description": (
                                "Global Z elevation of this level's (0,0,0) "
                                "point in mm; negative for basements."
                            ),
                        },
                    },
                    "required": ["label", "elevation_mm"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["site_label", "building_label", "levels"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    site_label: str,
    building_label: str,
    levels: list[dict[str, Any]],
) -> dict[str, Any]:
    clean_site = str(site_label or "").strip()
    clean_building = str(building_label or "").strip()
    if not clean_site:
        return _invalid("site_label is required.")
    if not clean_building:
        return _invalid("building_label is required.")
    if not isinstance(levels, list) or not levels:
        return _invalid("levels must contain at least one level.")
    level_specs: list[tuple[str, float]] = []
    for index, spec in enumerate(levels):
        if not isinstance(spec, dict):
            return _invalid(f"levels[{index}] must be an object.")
        label = str(spec.get("label") or "").strip()
        if not label:
            return _invalid(f"levels[{index}].label is required.")
        level_specs.append((label, float(spec.get("elevation_mm") or 0.0)))
    seen = set()
    for label, _elevation in level_specs:
        if label in seen:
            return _invalid(f"Duplicate level label: '{label}'.")
        seen.add(label)

    def create() -> dict[str, Any]:
        import Arch
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        stages: list[dict[str, Any]] = []
        building = None
        site = None
        level_objects = []
        try:
            building = Arch.makeBuilding([], name=clean_building)
            if building is None:
                raise RuntimeError("Arch.makeBuilding returned no object.")
            stages.append({"index": 0, "stage": "building", "status": "created", "object": building.Name})
            site = Arch.makeSite([building], name=clean_site)
            if site is None:
                raise RuntimeError("Arch.makeSite returned no object.")
            stages.append({"index": 1, "stage": "site", "status": "created", "object": site.Name})
            for level_index, (level_label, elevation) in enumerate(level_specs):
                level = Arch.makeFloor(name=level_label)
                if level is None:
                    raise RuntimeError(f"Arch.makeFloor returned no object for {level_label!r}.")
                level.Placement = App.Placement(
                    App.Vector(0.0, 0.0, elevation), App.Rotation()
                )
                building.addObject(level)
                doc.recompute()
                level_objects.append(level)
                stages.append(
                    {
                        "index": level_index + 2,
                        "stage": "level",
                        "status": "created",
                        "object": level.Name,
                        "requested_label": level_label,
                        "requested_elevation_mm": elevation,
                    }
                )
        except Exception as exc:
            stages.append(
                {
                    "index": len(stages),
                    "stage": "spatial_structure",
                    "status": "failed",
                    "native_error": str(exc),
                }
            )
        doc.recompute()
        level_summaries = [_level_summary(level) for level in level_objects]
        return {
            "document": doc.Name,
            "site": _spatial_summary(site),
            "building": _spatial_summary(building),
            "levels": level_summaries,
            "stages": stages,
            "retained_prefix": [stage for stage in stages if stage.get("status") == "created"],
            "failed_stage": next((stage for stage in stages if stage.get("status") == "failed"), None),
            "hierarchy": _hierarchy_summary(site, building, level_objects),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        hierarchy = result.get("hierarchy") or {}
        levels_actual = list(result.get("levels") or [])
        checks = [
            {
                "name": "all_stages_created",
                "ok": result.get("failed_stage") is None
                and len(levels_actual) == len(level_specs),
                "failed_stage": result.get("failed_stage"),
                "retained_prefix": result.get("retained_prefix"),
            },
            {
                "name": "site_building_hierarchy",
                "ok": hierarchy.get("building_in_site") is True
                and hierarchy.get("all_levels_in_building") is True,
                "actual": hierarchy,
            },
            {
                "name": "level_types_and_elevations",
                "ok": all(
                    level.get("ifc_type") == "Building Storey"
                    and abs(float(level.get("actual_elevation_mm", 0.0)) - level_specs[index][1]) <= 1.0e-9
                    for index, level in enumerate(levels_actual)
                ),
                "actual": levels_actual,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create BIM spatial structure: {clean_site}",
        create,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "create_spatial_structure", **result},
        next_action=(
            "Create walls with bim.create_wall from exact baseline objects "
            "already present in the document and assign them to the new levels."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _spatial_summary(obj: Any) -> dict[str, Any] | None:
    if obj is None:
        return None
    return {
        "object_name": obj.Name,
        "label": obj.Label,
        "type": obj.TypeId,
        "ifc_type": str(getattr(obj, "IfcType", "") or ""),
        "children": [child.Name for child in list(getattr(obj, "Group", []) or [])],
    }


def _level_summary(level: Any) -> dict[str, Any]:
    return {
        "object_name": level.Name,
        "label": level.Label,
        "type": level.TypeId,
        "ifc_type": str(getattr(level, "IfcType", "") or ""),
        "actual_elevation_mm": float(level.Placement.Base.z),
        "global_placement": domain_runtime.global_placement_summary(level),
        "children": [child.Name for child in list(getattr(level, "Group", []) or [])],
    }


def _hierarchy_summary(site: Any, building: Any, levels: list[Any]) -> dict[str, Any]:
    site_children = [child.Name for child in list(getattr(site, "Group", []) or [])] if site else []
    building_children = [
        child.Name for child in list(getattr(building, "Group", []) or [])
    ] if building else []
    return {
        "site_children": site_children,
        "building_children": building_children,
        "building_in_site": bool(building is not None and building.Name in site_children),
        "all_levels_in_building": bool(levels)
        and all(level.Name in building_children for level in levels),
    }
