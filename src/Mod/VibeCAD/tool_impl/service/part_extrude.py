# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Part extrusion from an exact profile object."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "part.extrude",
    "description": (
        "Create one native Part extrusion from an exact named 2D profile object "
        "(a sketch, Draft wire, or planar face). The profile becomes a child of "
        "the result and stays parametric. Closed profiles can produce solids; "
        "open profiles produce shells."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "profile_object_name": {
                "type": "string",
                "description": "Exact internal name of the 2D profile object.",
            },
            "direction": domain_runtime.vector_schema(
                "Extrusion direction as a global vector; it is normalized, so only "
                "the direction matters.",
                units=None,
            ),
            "extent": {
                "description": "Choose exactly one extrusion extent definition.",
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "one_direction"},
                            "length_mm": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["type", "length_mm"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "two_directions"},
                            "forward_mm": {"type": "number", "minimum": 0},
                            "reverse_mm": {"type": "number", "minimum": 0},
                        },
                        "required": ["type", "forward_mm", "reverse_mm"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "symmetric"},
                            "total_length_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": "Total distance centered on the profile plane.",
                            },
                        },
                        "required": ["type", "total_length_mm"],
                        "additionalProperties": False,
                    },
                ],
            },
            "solid": {
                "type": "boolean",
                "description": (
                    "true caps closed profiles into a solid; false leaves an open "
                    "shell. Requires a closed profile when true."
                ),
            },
            "taper_angle_degrees": {
                "type": "number",
                "minimum": -80,
                "maximum": 80,
                "description": (
                    "Draft angle applied along the extrusion in degrees; 0 for "
                    "straight walls."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new extrusion.",
            },
        },
        "required": [
            "profile_object_name",
            "direction",
            "extent",
            "solid",
            "taper_angle_degrees",
            "label",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    profile_object_name: str,
    direction: dict[str, Any],
    extent: dict[str, Any],
    solid: bool,
    taper_angle_degrees: float,
    label: str,
    second_taper_angle_degrees: float = 0.0,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    profile_name = str(profile_object_name or "").strip()
    doc = service._active_document()
    profile = doc.getObject(profile_name) if doc is not None and profile_name else None
    if profile is None:
        return _invalid(
            f"Profile object not found by exact internal name: {profile_object_name}",
            candidates=_profile_candidates(doc),
        )
    shape = getattr(profile, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(f"Profile object has no shape geometry: {profile_name}")
    extent_state = _extent_values(extent)
    if not extent_state.get("ok"):
        return extent_state
    forward = float(extent_state["forward_mm"])
    reverse = float(extent_state["reverse_mm"])
    symmetric = bool(extent_state["symmetric"])
    try:
        requested_vector = domain_runtime.parse_vector(direction)
    except Exception as exc:
        return _invalid("direction is not a valid vector.", native_error=str(exc))
    direction_state = domain_runtime.normalized_vector_summary(requested_vector)
    if not direction_state.get("ok"):
        return _invalid("direction must be a non-zero vector.", direction=direction_state)
    profile_diagnostics = domain_runtime.shape_profile_diagnostics(profile)
    if not profile_diagnostics.get("planar"):
        return _invalid(
            "The extrusion profile is not planar; no feature was created.",
            profile=profile_diagnostics,
        )
    if bool(solid) and not profile_diagnostics.get("face_buildable"):
        return _invalid(
            "solid=true requires closed, valid wires that build a planar face; no feature was created.",
            profile=profile_diagnostics,
        )
    visibility_before = domain_runtime.view_visibility_summary(profile)

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        base = active.getObject(profile_name)
        if base is None:
            raise RuntimeError("The profile object no longer exists.")
        vector = domain_runtime.parse_vector(direction)
        vector.normalize()
        extrusion = active.addObject("Part::Extrusion", "Extrude")
        extrusion.Label = clean_label
        extrusion.Base = base
        extrusion.DirMode = "Custom"
        extrusion.Dir = vector
        extrusion.LengthFwd = forward
        extrusion.LengthRev = reverse
        extrusion.Solid = bool(solid)
        extrusion.Symmetric = bool(symmetric)
        extrusion.TaperAngle = float(taper_angle_degrees)
        extrusion.TaperAngleRev = float(second_taper_angle_degrees)
        domain_runtime.adopt_part_result(extrusion)
        active.recompute()
        view = getattr(base, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            view.Visibility = False
        actual_direction = domain_runtime.normalized_vector_summary(extrusion.Dir)
        return {
            "document": active.Name,
            "feature": extrusion.Name,
            "feature_label": extrusion.Label,
            "feature_type": extrusion.TypeId,
            "profile_object": base.Name,
            "profile_diagnostics": profile_diagnostics,
            "requested_direction": direction_state,
            "actual_direction": actual_direction,
            "requested_extent": dict(extent),
            "actual_extent": {
                "length_forward_mm": float(extrusion.LengthFwd),
                "length_reverse_mm": float(extrusion.LengthRev),
                "symmetric": bool(extrusion.Symmetric),
            },
            "solid_requested": bool(solid),
            "solid_count": len(list(getattr(extrusion.Shape, "Solids", []) or [])),
            "source_visibility_before": visibility_before,
            "source_visibility_after": domain_runtime.view_visibility_summary(base),
            "shape": domain_runtime.shape_summary(extrusion),
            "feature_state": domain_runtime.feature_state_summary(extrusion),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        visibility = result.get("source_visibility_after") or {}
        checks = [
            {
                "name": "requested_solid_result",
                "ok": not bool(solid) or int(result.get("solid_count", 0)) > 0,
                "expected": "at least one solid" if solid else "shell or solid",
                "actual_solid_count": int(result.get("solid_count", 0)),
            },
            {
                "name": "source_visibility",
                "ok": not visibility.get("supported") or visibility.get("visible") is False,
                "actual": visibility,
            },
            {
                "name": "direction_readback",
                "ok": bool((result.get("actual_direction") or {}).get("ok")),
                "actual": result.get("actual_direction"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create Part extrusion: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation="extrude")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _extent_values(extent: Any) -> dict[str, Any]:
    if not isinstance(extent, dict):
        return _invalid("extent must select one explicit extent definition.")
    kind = str(extent.get("type") or "")
    if kind == "one_direction":
        length = float(extent.get("length_mm", 0.0))
        if length <= 0:
            return _invalid("extent.length_mm must be greater than 0.")
        return {"ok": True, "forward_mm": length, "reverse_mm": 0.0, "symmetric": False}
    if kind == "two_directions":
        forward = float(extent.get("forward_mm", 0.0))
        reverse = float(extent.get("reverse_mm", 0.0))
        if forward <= 0 and reverse <= 0:
            return _invalid("At least one two-direction extent must be greater than 0.")
        return {"ok": True, "forward_mm": forward, "reverse_mm": reverse, "symmetric": False}
    if kind == "symmetric":
        total = float(extent.get("total_length_mm", 0.0))
        if total <= 0:
            return _invalid("extent.total_length_mm must be greater than 0.")
        return {"ok": True, "forward_mm": total, "reverse_mm": 0.0, "symmetric": True}
    return _invalid("extent.type must be one_direction, two_directions, or symmetric.")


def _profile_candidates(doc: Any) -> list[dict[str, Any]]:
    if doc is None:
        return []
    candidates = []
    for obj in list(getattr(doc, "Objects", []) or []):
        shape = getattr(obj, "Shape", None)
        if shape is None or bool(shape.isNull()):
            continue
        candidates.append(
            {
                "name": obj.Name,
                "label": obj.Label,
                "type": obj.TypeId,
                "shape": domain_runtime.shape_summary(obj),
            }
        )
    return candidates[:30]
