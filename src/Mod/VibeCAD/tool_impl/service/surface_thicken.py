# SPDX-License-Identifier: LGPL-2.1-or-later

"""Thicken one exact surface into a solid with uniform wall thickness."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "surface.thicken",
    "description": (
        "Create one solid by offsetting an exact named surface (shell or face) "
        "by a uniform thickness and filling the gap. This is how a modeled "
        "surface becomes a manufacturable solid wall. The source surface "
        "becomes a hidden child of the result."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "SurfaceWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the surface object to thicken."
                ),
            },
            "thickness_mm": {
                "type": "number",
                "description": (
                    "Wall thickness in mm; positive offsets along the surface "
                    "normal, negative offsets against it. Must be non-zero."
                ),
            },
            "join_style": {
                "type": "string",
                "enum": ["arc", "intersection"],
                "description": (
                    "How offset walls meet at edges: 'arc' rounds the joint, "
                    "'intersection' extends walls to a sharp joint."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the thickened solid.",
            },
        },
        "required": ["object_name", "thickness_mm", "join_style", "label"],
        "additionalProperties": False,
    },
}


JOIN_STYLE_TO_NATIVE = {
    "arc": "Arc",
    "intersection": "Intersection",
}


def run(
    service: Any,
    object_name: str,
    thickness_mm: float,
    join_style: str,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    thickness = float(thickness_mm)
    if abs(thickness) < 1e-9:
        return _invalid("thickness_mm must be non-zero.")
    native_join = JOIN_STYLE_TO_NATIVE.get(str(join_style or "").strip())
    if native_join is None:
        allowed = ", ".join(sorted(JOIN_STYLE_TO_NATIVE))
        return _invalid(f"join_style must be one of: {allowed}.")
    clean_name = str(object_name or "").strip()
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {object_name}")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(f"Object has no shape geometry: {clean_name}")
    if not (getattr(shape, "Faces", []) or []):
        return _invalid(
            f"Object has no faces to thicken: {clean_name}. "
            "Thicken needs a surface or shell, not a bare curve."
        )
    source_health = domain_runtime.shape_health(obj)
    if not source_health.get("valid_non_null"):
        return _invalid("The source surface shape is invalid.", source=source_health)
    source_topology = {
        "face_count": len(list(getattr(shape, "Faces", []) or [])),
        "shell_count": len(list(getattr(shape, "Shells", []) or [])),
        "shells_closed": [
            bool(shell.isClosed()) for shell in list(getattr(shape, "Shells", []) or [])
        ],
    }
    visibility_before = domain_runtime.view_visibility_summary(obj)

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        base = active.getObject(clean_name)
        if base is None:
            raise RuntimeError("The object no longer exists.")
        offset = active.addObject("Part::Offset", "Thicken")
        offset.Label = clean_label
        offset.Source = base
        offset.Value = thickness
        offset.Mode = "Skin"
        offset.Join = native_join
        offset.Fill = True
        offset.Intersection = False
        offset.SelfIntersection = False
        domain_runtime.mark_modeling_replaced_inputs(
            offset,
            [base] if visibility_before.get("visible") is True else [],
        )
        active.recompute()
        view = getattr(base, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            view.Visibility = False
        return {
            "document": active.Name,
            "feature": offset.Name,
            "feature_label": offset.Label,
            "feature_type": offset.TypeId,
            "source_object": base.Name,
            "source_shape": source_health,
            "source_topology": source_topology,
            "requested_thickness_mm": thickness,
            "requested_join_style": str(join_style),
            "actual_offset_properties": {
                "source": getattr(getattr(offset, "Source", None), "Name", None),
                "value_mm": float(offset.Value),
                "mode": str(offset.Mode),
                "join": str(offset.Join),
                "fill": bool(offset.Fill),
                "intersection": bool(offset.Intersection),
                "self_intersection": bool(offset.SelfIntersection),
            },
            "source_visibility_before": visibility_before,
            "source_visibility_after": domain_runtime.view_visibility_summary(base),
            "actual_solid_count": len(list(getattr(offset.Shape, "Solids", []) or [])),
            "actual_shell_count": len(list(getattr(offset.Shape, "Shells", []) or [])),
            "shape": domain_runtime.shape_summary(offset),
            "feature_state": domain_runtime.feature_state_summary(offset),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        shape = result.get("shape") or {}
        state = result.get("feature_state") or {}
        visibility = result.get("source_visibility_after") or {}
        actual = result.get("actual_offset_properties") or {}
        checks = [
            {
                "name": "exactly_one_valid_solid",
                "ok": int(result.get("actual_solid_count", 0)) == 1
                and state.get("shape_valid") is True
                and not state.get("marked_invalid"),
                "actual_shape": shape,
                "actual_solid_count": result.get("actual_solid_count"),
                "actual_shell_count": result.get("actual_shell_count"),
            },
            {
                "name": "offset_readback",
                "ok": actual.get("source") == clean_name
                and abs(float(actual.get("value_mm", 0.0)) - thickness) <= 1.0e-9
                and actual.get("fill") is True,
                "actual": actual,
            },
            {
                "name": "source_visibility",
                "ok": not visibility.get("supported") or visibility.get("visible") is False,
                "actual": visibility,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Thicken surface: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation="surface_thicken")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
