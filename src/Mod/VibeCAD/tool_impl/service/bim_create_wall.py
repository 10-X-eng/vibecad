# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native BIM wall from an exact baseline object."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "bim.create_wall",
    "description": (
        "Create one native BIM wall by extruding an exact baseline object "
        "(an existing Draft wire or line) upward. The wall "
        "follows the baseline path with the given height and thickness; the "
        "baseline object is consumed as the wall's base and hidden. Draw the "
        "baseline at the level's floor elevation first."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "BIMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "baseline_object": {
                "type": "string",
                "description": (
                    "Exact internal name of the existing baseline object "
                    "(for example a Draft wire named 'Wire') the wall follows."
                ),
            },
            "height_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Wall height above the baseline in mm.",
            },
            "thickness_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Wall thickness in mm.",
            },
            "alignment": {
                "type": "string",
                "enum": ["center", "left", "right"],
                "description": (
                    "Which side of the baseline the wall thickness grows "
                    "toward: 'center' splits it evenly, 'left'/'right' place "
                    "the full thickness on that side of the baseline "
                    "direction."
                ),
            },
            "level_assignment": {
                "description": (
                    "Assign the wall to no level or to one exact building-storey "
                    "object by internal name."
                ),
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"type": {"const": "none"}},
                        "required": ["type"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "building_storey"},
                            "object_name": {"type": "string"},
                        },
                        "required": ["type", "object_name"],
                        "additionalProperties": False,
                    },
                ],
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new wall, e.g. 'NorthWall'.",
            },
        },
        "required": [
            "baseline_object",
            "height_mm",
            "thickness_mm",
            "alignment",
            "level_assignment",
            "label",
        ],
        "additionalProperties": False,
    },
}

_ALIGNMENTS = {"center": "Center", "left": "Left", "right": "Right"}


def run(
    service: Any,
    baseline_object: str,
    height_mm: float,
    thickness_mm: float,
    alignment: str,
    level_assignment: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    baseline_name = str(baseline_object or "").strip()
    if not baseline_name:
        return _invalid("baseline_object is required.")
    height = float(height_mm)
    thickness = float(thickness_mm)
    if height <= 0:
        return _invalid("height_mm must be greater than 0.")
    if thickness <= 0:
        return _invalid("thickness_mm must be greater than 0.")
    align = _ALIGNMENTS.get(str(alignment or ""))
    if align is None:
        return _invalid("alignment must be center, left, or right.")
    doc = service._active_document()
    baseline = doc.getObject(baseline_name) if doc is not None else None
    if baseline is None:
        return _invalid(
            f"Baseline object not found by exact internal name: {baseline_name}",
            candidates=[
                {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
                for obj in list(getattr(doc, "Objects", []) or [])
                if getattr(obj, "Shape", None) is not None
            ][:40],
        )
    baseline_diagnostics = _baseline_diagnostics(baseline)
    if not baseline_diagnostics.get("ok"):
        return _invalid(
            "The baseline does not form one valid, connected, level wire path.",
            baseline=baseline_diagnostics,
        )
    level_state = _resolve_level(doc, level_assignment)
    if not level_state.get("ok"):
        return level_state
    level_name = level_state.get("object_name") or ""
    visibility_before = domain_runtime.view_visibility_summary(baseline)

    def create() -> dict[str, Any]:
        import Arch
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        native_baseline = doc.getObject(baseline_name)
        if native_baseline is None:
            raise RuntimeError(
                f"Baseline object '{baseline_name}' not found; provide an "
                "exact existing document object name."
            )
        shape = getattr(native_baseline, "Shape", None)
        if shape is None or not getattr(shape, "Edges", []):
            raise RuntimeError(
                f"Baseline object '{baseline_name}' has no edges to follow; "
                "provide an existing wire or line baseline."
            )
        level = None
        if level_name:
            level = doc.getObject(level_name)
            if level is None:
                raise RuntimeError(
                    f"Level object '{level_name}' not found; use "
                    "bim.list_structure for exact names."
                )
        wall = Arch.makeWall(
            native_baseline,
            height=height,
            width=thickness,
            align=align,
            name=clean_label,
        )
        if wall is None:
            raise RuntimeError("Arch.makeWall did not create an object.")
        if level is not None:
            level.addObject(wall)
        doc.recompute()
        return {
            "document": doc.Name,
            "feature": wall.Name,
            "feature_label": wall.Label,
            "feature_type": wall.TypeId,
            "ifc_type": getattr(wall, "IfcType", None),
            "baseline_object": native_baseline.Name,
            "level_object": level.Name if level is not None else None,
            "baseline_diagnostics": baseline_diagnostics,
            "requested_dimensions": {"height_mm": height, "thickness_mm": thickness},
            "actual_dimensions": {
                "height_mm": float(getattr(wall, "Height", 0.0)),
                "thickness_mm": float(getattr(wall, "Width", 0.0)),
                "alignment": str(getattr(wall, "Align", "")),
            },
            "native_base_link": getattr(getattr(wall, "Base", None), "Name", None),
            "level_members": [
                child.Name for child in list(getattr(level, "Group", []) or [])
            ] if level is not None else [],
            "baseline_visibility_before": visibility_before,
            "baseline_visibility_after": domain_runtime.view_visibility_summary(native_baseline),
            "shape": domain_runtime.shape_summary(wall),
            "feature_state": domain_runtime.feature_state_summary(wall),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        actual = result.get("actual_dimensions") or {}
        visibility = result.get("baseline_visibility_after") or {}
        checks = [
            {
                "name": "wall_dimensions_and_alignment",
                "ok": abs(float(actual.get("height_mm", 0.0)) - height) <= 1.0e-9
                and abs(float(actual.get("thickness_mm", 0.0)) - thickness) <= 1.0e-9
                and actual.get("alignment") == align,
                "expected": {"height_mm": height, "thickness_mm": thickness, "alignment": align},
                "actual": actual,
            },
            {
                "name": "baseline_link",
                "ok": result.get("native_base_link") == baseline_name,
                "expected": baseline_name,
                "actual": result.get("native_base_link"),
            },
            {
                "name": "level_membership",
                "ok": not level_name or result.get("feature") in list(result.get("level_members") or []),
                "actual": result.get("level_members"),
            },
            {
                "name": "baseline_visibility",
                "ok": not visibility.get("supported") or visibility.get("visible") is False,
                "actual": visibility,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create BIM wall: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(
        transaction,
        operation="create_wall",
        next_action=(
            "Add openings with bim.add_window, or create the next wall; "
            "capture a screenshot to verify placement."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _baseline_diagnostics(baseline: Any) -> dict[str, Any]:
    shape = getattr(baseline, "Shape", None)
    if shape is None or bool(shape.isNull()):
        return {"ok": False, "failure": "shape_is_null"}
    wires = list(getattr(shape, "Wires", []) or [])
    edges = list(getattr(shape, "Edges", []) or [])
    vertices = list(getattr(shape, "Vertexes", []) or [])
    z_values = [float(vertex.Point.z) for vertex in vertices]
    level_deviation = max(z_values, default=0.0) - min(z_values, default=0.0)
    return {
        "ok": len(wires) == 1
        and len(edges) > 0
        and bool(wires[0].isValid())
        and level_deviation <= 1.0e-7,
        "shape": domain_runtime.shape_summary(baseline),
        "wire_count": len(wires),
        "edge_count": len(edges),
        "wire_closed": bool(wires[0].isClosed()) if len(wires) == 1 else None,
        "wire_valid": bool(wires[0].isValid()) if len(wires) == 1 else None,
        "elevation_range_mm": {"minimum": min(z_values, default=None), "maximum": max(z_values, default=None)},
        "level_deviation_mm": level_deviation,
    }


def _resolve_level(doc: Any, assignment: Any) -> dict[str, Any]:
    if not isinstance(assignment, dict):
        return _invalid("level_assignment must select none or building_storey.")
    kind = str(assignment.get("type") or "")
    if kind == "none":
        return {"ok": True, "object_name": None}
    if kind != "building_storey":
        return _invalid("level_assignment.type must be none or building_storey.")
    name = str(assignment.get("object_name") or "").strip()
    level = doc.getObject(name) if doc is not None and name else None
    if level is None or str(getattr(level, "IfcType", "")) != "Building Storey":
        return _invalid(
            "level_assignment.object_name must identify a native Building Storey.",
            requested=name,
            candidates=[
                {"name": obj.Name, "label": obj.Label, "type": obj.TypeId, "ifc_type": str(getattr(obj, "IfcType", ""))}
                for obj in list(getattr(doc, "Objects", []) or [])
                if str(getattr(obj, "IfcType", "")) == "Building Storey"
            ],
        )
    return {"ok": True, "object_name": level.Name, "level": level}
