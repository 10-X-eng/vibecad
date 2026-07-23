# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Part revolution from an exact profile object."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "part.revolve",
    "description": (
        "Create one native Part revolution by sweeping an exact named 2D profile "
        "object around a global axis. The profile becomes a child of the result and "
        "stays parametric. The profile must not cross the axis."
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
            "axis_point": domain_runtime.vector_schema(
                "A global point on the revolution axis in mm."
            ),
            "axis_direction": domain_runtime.vector_schema(
                "Direction of the revolution axis; only the direction matters.",
                units=None,
            ),
            "angle_degrees": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 360,
                "description": "Sweep angle in degrees; 360 for a full revolution.",
            },
            "symmetric": {
                "type": "boolean",
                "description": (
                    "Center the requested angle on the profile instead of revolving in one "
                    "direction. Omit or use false for historical one-direction behavior."
                ),
            },
            "solid": {
                "type": "boolean",
                "description": (
                    "true caps a closed profile into a solid; false leaves an open "
                    "shell."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new revolution.",
            },
        },
        "required": [
            "profile_object_name",
            "axis_point",
            "axis_direction",
            "angle_degrees",
            "solid",
            "label",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    profile_object_name: str,
    axis_point: dict[str, Any],
    axis_direction: dict[str, Any],
    angle_degrees: float,
    solid: bool,
    label: str,
    symmetric: bool = False,
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
    try:
        requested_axis_point = domain_runtime.parse_vector(axis_point)
        requested_axis = domain_runtime.parse_vector(axis_direction)
    except Exception as exc:
        return _invalid("The revolution axis is not a valid point/direction pair.", native_error=str(exc))
    axis_state = domain_runtime.normalized_vector_summary(requested_axis)
    if not axis_state.get("ok"):
        return _invalid("axis_direction must be a non-zero vector.", axis=axis_state)
    profile_diagnostics = domain_runtime.shape_profile_diagnostics(profile)
    if not profile_diagnostics.get("planar"):
        return _invalid(
            "The revolution profile is not planar; no feature was created.",
            profile=profile_diagnostics,
        )
    if bool(solid) and not profile_diagnostics.get("face_buildable"):
        return _invalid(
            "solid=true requires closed, valid wires that build a planar face; no feature was created.",
            profile=profile_diagnostics,
        )
    axis_relationship = _axis_profile_relationship(
        profile,
        requested_axis_point,
        requested_axis,
        profile_diagnostics,
    )
    if not axis_relationship.get("axis_in_profile_plane"):
        return _invalid(
            "The revolution axis must lie in the profile plane; no feature was created.",
            profile=profile_diagnostics,
            axis_relationship=axis_relationship,
        )
    if axis_relationship.get("profile_crosses_axis"):
        return _invalid(
            "The profile crosses the revolution axis; no feature was created.",
            profile=profile_diagnostics,
            axis_relationship=axis_relationship,
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
        axis_vector = domain_runtime.parse_vector(axis_direction)
        axis_vector.normalize()
        revolution = active.addObject("Part::Revolution", "Revolve")
        revolution.Label = clean_label
        revolution.Source = base
        revolution.Base = domain_runtime.parse_vector(axis_point)
        revolution.Axis = axis_vector
        revolution.Angle = float(angle_degrees)
        revolution.Symmetric = bool(symmetric)
        revolution.Solid = bool(solid)
        domain_runtime.adopt_part_result(revolution)
        active.recompute()
        view = getattr(base, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            view.Visibility = False
        return {
            "document": active.Name,
            "feature": revolution.Name,
            "feature_label": revolution.Label,
            "feature_type": revolution.TypeId,
            "profile_object": base.Name,
            "profile_diagnostics": profile_diagnostics,
            "axis_relationship": axis_relationship,
            "requested_axis": {
                "point": domain_runtime.vector_values(requested_axis_point),
                **axis_state,
            },
            "actual_axis": {
                "point": domain_runtime.vector_values(revolution.Base),
                **domain_runtime.normalized_vector_summary(revolution.Axis),
            },
            "requested_angle_degrees": float(angle_degrees),
            "actual_angle_degrees": float(revolution.Angle),
            "symmetric_requested": bool(symmetric),
            "symmetric_actual": bool(revolution.Symmetric),
            "solid_requested": bool(solid),
            "solid_count": len(list(getattr(revolution.Shape, "Solids", []) or [])),
            "source_visibility_before": visibility_before,
            "source_visibility_after": domain_runtime.view_visibility_summary(base),
            "shape": domain_runtime.shape_summary(revolution),
            "feature_state": domain_runtime.feature_state_summary(revolution),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        visibility = result.get("source_visibility_after") or {}
        checks = [
            {
                "name": "requested_solid_result",
                "ok": not bool(solid) or int(result.get("solid_count", 0)) > 0,
                "actual_solid_count": int(result.get("solid_count", 0)),
            },
            {
                "name": "source_visibility",
                "ok": not visibility.get("supported") or visibility.get("visible") is False,
                "actual": visibility,
            },
            {
                "name": "axis_readback",
                "ok": bool((result.get("actual_axis") or {}).get("ok")),
                "actual": result.get("actual_axis"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create Part revolution: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation="revolve")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _axis_profile_relationship(
    profile: Any,
    axis_point: Any,
    axis_direction: Any,
    profile_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    import FreeCAD as App
    import Part

    normalized_axis = App.Vector(axis_direction)
    normalized_axis.normalize()
    plane = profile_diagnostics.get("plane") or {}
    plane_origin = App.Vector(*(plane.get("origin") or [0.0, 0.0, 0.0]))
    plane_normal = App.Vector(*(plane.get("normal") or [0.0, 0.0, 1.0]))
    point_plane_distance = abs(float((axis_point - plane_origin).dot(plane_normal)))
    direction_plane_dot = abs(float(normalized_axis.dot(plane_normal)))
    axis_in_plane = point_plane_distance <= 1.0e-7 and direction_plane_dot <= 1.0e-9
    radial = plane_normal.cross(normalized_axis)
    signed_distances: list[float] = []
    if float(radial.Length) > 1.0e-12:
        radial.normalize()
        for vertex in list(getattr(profile.Shape, "Vertexes", []) or []):
            signed_distances.append(float((vertex.Point - axis_point).dot(radial)))
    minimum = min(signed_distances, default=None)
    maximum = max(signed_distances, default=None)
    crosses = bool(
        minimum is not None
        and maximum is not None
        and minimum < -1.0e-7
        and maximum > 1.0e-7
    )
    intersections: list[list[float]] = []
    intersection_error = None
    try:
        bounds = profile.Shape.BoundBox
        span = max(float(bounds.DiagonalLength) * 4.0, 1000.0)
        axis_edge = Part.makeLine(
            axis_point - normalized_axis * span,
            axis_point + normalized_axis * span,
        )
        section = profile.Shape.section(axis_edge)
        intersections = [
            domain_runtime.vector_values(vertex.Point)
            for vertex in list(getattr(section, "Vertexes", []) or [])
        ]
    except Exception as exc:
        intersection_error = str(exc)
    return {
        "axis_in_profile_plane": axis_in_plane,
        "axis_point_plane_distance_mm": point_plane_distance,
        "axis_direction_plane_normal_abs_dot": direction_plane_dot,
        "signed_vertex_distance_range_mm": {"minimum": minimum, "maximum": maximum},
        "profile_crosses_axis": crosses,
        "profile_axis_intersections": intersections,
        "intersection_algorithm": "BRepAlgoAPI_Section",
        "intersection_error": intersection_error,
    }


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
