# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native BIM structural element (column, beam, or slab)."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "bim.create_structure",
    "description": (
        "Create one native BIM structural element: a vertical column, a "
        "horizontal beam, or a slab. Columns and beams are rectangular boxes "
        "placed at an exact position (the corner with the smallest X/Y/Z). "
        "Slabs extrude an exact closed planar profile object downward by "
        "their thickness, so draw the profile with make_face=true at the "
        "level's floor elevation first. Each element is classified with the "
        "matching IFC type."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "BIMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "element": {
                "description": (
                    "Structural element to create; choose exactly one variant."
                ),
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "column",
                                "description": "Vertical column extruded along Z.",
                            },
                            "width_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Column cross-section size along X in mm."
                                ),
                            },
                            "depth_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Column cross-section size along Y in mm."
                                ),
                            },
                            "height_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": "Column height along Z in mm.",
                            },
                            "position": domain_runtime.vector_schema(
                                "Global position of the column's minimum-X/Y/Z "
                                "corner in mm."
                            ),
                        },
                        "required": [
                            "type",
                            "width_mm",
                            "depth_mm",
                            "height_mm",
                            "position",
                        ],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "beam",
                                "description": (
                                    "Horizontal beam spanning along the X axis."
                                ),
                            },
                            "span_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": "Beam span along X in mm.",
                            },
                            "width_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Beam cross-section size along Y in mm."
                                ),
                            },
                            "depth_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Beam cross-section size along Z (vertical "
                                    "depth) in mm."
                                ),
                            },
                            "start_position": domain_runtime.vector_schema(
                                "Global position of the beam's start-section minimum corner in mm."
                            ),
                            "direction": domain_runtime.vector_schema(
                                "Global beam span direction; normalized before use.",
                                units=None,
                            ),
                        },
                        "required": [
                            "type",
                            "span_mm",
                            "width_mm",
                            "depth_mm",
                            "start_position",
                            "direction",
                        ],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "slab",
                                "description": (
                                    "Floor/ceiling slab extruded downward from "
                                    "an exact planar profile."
                                ),
                            },
                            "profile_object": {
                                "type": "string",
                                "description": (
                                    "Exact internal name of the closed planar "
                                    "profile object already present in the "
                                    "document and outlining the slab; the "
                                    "profile is consumed and hidden."
                                ),
                            },
                            "thickness_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Slab thickness in mm, extruded downward "
                                    "so the slab top sits at the profile "
                                    "plane."
                                ),
                            },
                        },
                        "required": ["type", "profile_object", "thickness_mm"],
                        "additionalProperties": False,
                    },
                ],
            },
            "level_assignment": {
                "description": (
                    "Assign the element to no level or to one exact building-storey "
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
                "description": (
                    "Visible label for the new element, e.g. 'CornerColumn'."
                ),
            },
        },
        "required": ["element", "level_assignment", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    element: dict[str, Any],
    level_assignment: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    if not isinstance(element, dict):
        return _invalid("element must be an object.")
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    kind = str(element.get("type") or "")
    if kind == "column":
        dims = ("width_mm", "depth_mm", "height_mm")
    elif kind == "beam":
        dims = ("span_mm", "width_mm", "depth_mm")
    elif kind == "slab":
        dims = ("thickness_mm",)
    else:
        return _invalid("element.type must be column, beam, or slab.")
    values: dict[str, float] = {}
    for dim in dims:
        value = float(element.get(dim) or 0.0)
        if value <= 0:
            return _invalid(f"element.{dim} must be greater than 0.")
        values[dim] = value
    profile_name = str(element.get("profile_object") or "").strip()
    if kind == "slab" and not profile_name:
        return _invalid("element.profile_object is required for a slab.")
    position = element.get("position") if kind == "column" else element.get("start_position")
    if kind in ("column", "beam") and not isinstance(position, dict):
        return _invalid(
            f"element.{'position' if kind == 'column' else 'start_position'} is required for a {kind}."
        )
    direction_state = None
    if kind == "beam":
        direction_value = element.get("direction")
        if not isinstance(direction_value, dict):
            return _invalid("element.direction is required for a beam.")
        direction_state = domain_runtime.normalized_vector_summary(
            domain_runtime.parse_vector(direction_value)
        )
        if not direction_state.get("ok"):
            return _invalid("element.direction must be non-zero.", direction=direction_state)
    doc = service._active_document()
    from .bim_create_wall import _resolve_level

    level_state = _resolve_level(doc, level_assignment)
    if not level_state.get("ok"):
        return level_state
    level_name = level_state.get("object_name") or ""
    profile = doc.getObject(profile_name) if kind == "slab" and doc is not None else None
    profile_diagnostics = None
    visibility_before = None
    if kind == "slab":
        if profile is None:
            return _invalid(
                f"Profile object not found by exact internal name: {profile_name}",
                candidates=[
                    {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
                    for obj in list(getattr(doc, "Objects", []) or [])
                    if getattr(obj, "Shape", None) is not None
                ][:40],
            )
        profile_diagnostics = domain_runtime.shape_profile_diagnostics(profile)
        if (
            not profile_diagnostics.get("planar")
            or not profile_diagnostics.get("face_buildable")
            or int(profile_diagnostics.get("existing_face_count", 0)) != 1
        ):
            return _invalid(
                "A slab profile must be exactly one closed planar face region.",
                profile=profile_diagnostics,
            )
        visibility_before = domain_runtime.view_visibility_summary(profile)

    def create() -> dict[str, Any]:
        import Arch
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        level = None
        if level_name:
            level = doc.getObject(level_name)
            if level is None:
                raise RuntimeError(
                    f"Level object '{level_name}' not found; use "
                    "bim.list_structure for exact names."
                )
        native_profile = None
        if kind == "column":
            obj = Arch.makeStructure(
                length=values["width_mm"],
                width=values["depth_mm"],
                height=values["height_mm"],
                name=clean_label,
            )
        elif kind == "beam":
            obj = Arch.makeStructure(
                length=values["span_mm"],
                width=values["width_mm"],
                height=values["depth_mm"],
                name=clean_label,
            )
        else:
            native_profile = doc.getObject(profile_name)
            if native_profile is None:
                raise RuntimeError(
                    f"Profile object '{profile_name}' not found; provide an "
                    "exact existing document object name."
                )
            profile_shape = getattr(native_profile, "Shape", None)
            if profile_shape is None or not getattr(profile_shape, "Faces", []):
                raise RuntimeError(
                    f"Profile object '{profile_name}' has no planar face; "
                    "create it closed with make_face=true so the slab has an "
                    "outline to extrude."
                )
            obj = Arch.makeStructure(
                native_profile,
                height=values["thickness_mm"],
                name=clean_label,
            )
        if obj is None:
            raise RuntimeError("Arch.makeStructure did not create an object.")
        if kind == "slab":
            obj.Normal = App.Vector(0, 0, -1)
        else:
            rotation = App.Rotation()
            if kind == "beam":
                beam_direction = domain_runtime.parse_vector(element["direction"])
                beam_direction.normalize()
                rotation = App.Rotation(App.Vector(1, 0, 0), beam_direction)
            obj.Placement = App.Placement(
                domain_runtime.parse_vector(position), rotation
            )
        obj.IfcType = {"column": "Column", "beam": "Beam", "slab": "Slab"}[kind]
        obj.Label = clean_label
        if level is not None:
            level.addObject(obj)
        doc.recompute()
        return {
            "document": doc.Name,
            "feature": obj.Name,
            "feature_label": obj.Label,
            "feature_type": obj.TypeId,
            "ifc_type": getattr(obj, "IfcType", None),
            "element_type": kind,
            "profile_object": native_profile.Name if native_profile is not None else None,
            "level_object": level.Name if level is not None else None,
            "requested_element": dict(element),
            "profile_diagnostics": profile_diagnostics,
            "requested_beam_direction": direction_state,
            "actual_dimensions": {
                "length_mm": float(getattr(obj, "Length", 0.0)),
                "width_mm": float(getattr(obj, "Width", 0.0)),
                "height_mm": float(getattr(obj, "Height", 0.0)),
            },
            "actual_placement": domain_runtime.placement_summary(obj),
            "global_placement": domain_runtime.global_placement_summary(obj),
            "level_members": [
                child.Name for child in list(getattr(level, "Group", []) or [])
            ] if level is not None else [],
            "profile_visibility_before": visibility_before,
            "profile_visibility_after": domain_runtime.view_visibility_summary(native_profile)
            if native_profile is not None else None,
            "shape": domain_runtime.shape_summary(obj),
            "feature_state": domain_runtime.feature_state_summary(obj),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        expected_ifc = {"column": "Column", "beam": "Beam", "slab": "Slab"}[kind]
        dimensions = result.get("actual_dimensions") or {}
        if kind == "column":
            expected_dimensions = {
                "length_mm": values["width_mm"],
                "width_mm": values["depth_mm"],
                "height_mm": values["height_mm"],
            }
        elif kind == "beam":
            expected_dimensions = {
                "length_mm": values["span_mm"],
                "width_mm": values["width_mm"],
                "height_mm": values["depth_mm"],
            }
        else:
            expected_dimensions = {"height_mm": values["thickness_mm"]}
        checks = [
            {
                "name": "ifc_classification",
                "ok": result.get("ifc_type") == expected_ifc,
                "expected": expected_ifc,
                "actual": result.get("ifc_type"),
            },
            {
                "name": "dimensions",
                "ok": all(
                    abs(float(dimensions.get(key, 0.0)) - value) <= 1.0e-9
                    for key, value in expected_dimensions.items()
                ),
                "expected": expected_dimensions,
                "actual": dimensions,
            },
            {
                "name": "level_membership",
                "ok": not level_name or result.get("feature") in list(result.get("level_members") or []),
                "actual": result.get("level_members"),
            },
        ]
        if kind == "slab":
            visibility = result.get("profile_visibility_after") or {}
            checks.append(
                {
                    "name": "profile_visibility",
                    "ok": not visibility.get("supported") or visibility.get("visible") is False,
                    "actual": visibility,
                }
            )
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create BIM {kind}: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(
        transaction,
        operation=f"create_{kind}",
        next_action=(
            "Verify the returned placement or capture a screenshot, "
            "then continue placing elements."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
