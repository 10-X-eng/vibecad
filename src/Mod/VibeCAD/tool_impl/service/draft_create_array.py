# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Draft array (orthogonal or polar) of an exact object."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "draft.create_array",
    "description": (
        "Create one native Draft array of an exact named object: an orthogonal "
        "grid along explicit interval vectors, or a polar ring around an exact "
        "center. The array is one parametric object linked to the source; the "
        "source stays visible and unchanged."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "DraftWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "source_object_name": {
                "type": "string",
                "description": "Exact internal name of the object to replicate.",
            },
            "array": {
                "description": "Array layout; choose exactly one variant.",
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "orthogonal",
                                "description": (
                                    "Grid of copies along explicit interval vectors."
                                ),
                            },
                            "interval_x": domain_runtime.vector_schema(
                                "Displacement between consecutive copies along "
                                "the first grid direction in mm."
                            ),
                            "interval_y": domain_runtime.vector_schema(
                                "Displacement between consecutive copies along "
                                "the second grid direction in mm; use {0,0,0} "
                                "with count_y=1 for a single row."
                            ),
                            "count_x": {
                                "type": "integer",
                                "minimum": 1,
                                "description": (
                                    "Copies along interval_x, including the original."
                                ),
                            },
                            "count_y": {
                                "type": "integer",
                                "minimum": 1,
                                "description": (
                                    "Copies along interval_y, including the "
                                    "original; 1 for a single row."
                                ),
                            },
                        },
                        "required": [
                            "type",
                            "interval_x",
                            "interval_y",
                            "count_x",
                            "count_y",
                        ],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "polar",
                                "description": (
                                    "Ring of copies rotated around a center point."
                                ),
                            },
                            "center": domain_runtime.vector_schema(
                                "Exact global center of rotation in mm; copies "
                                "rotate around the global Z axis through this "
                                "point."
                            ),
                            "count": {
                                "type": "integer",
                                "minimum": 2,
                                "description": (
                                    "Total copies including the original, spread "
                                    "over total_angle_degrees."
                                ),
                            },
                            "total_angle_degrees": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 360,
                                "description": (
                                    "Angular span of the ring in degrees; 360 "
                                    "for a full evenly spaced circle."
                                ),
                            },
                        },
                        "required": ["type", "center", "count", "total_angle_degrees"],
                        "additionalProperties": False,
                    },
                ],
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new array, e.g. 'BoltPattern'.",
            },
        },
        "required": ["source_object_name", "array", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    source_object_name: str,
    array: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if not isinstance(array, dict):
        return _invalid("array must be an object.")
    kind = str(array.get("type") or "")
    if kind not in ("orthogonal", "polar"):
        return _invalid("array.type must be orthogonal or polar.")
    source_name = str(source_object_name or "").strip()
    doc = service._active_document()
    source = doc.getObject(source_name) if doc is not None and source_name else None
    if source is None:
        return _invalid(
            f"Source object not found by exact internal name: {source_object_name}",
            candidates=[
                {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
                for obj in list(getattr(doc, "Objects", []) or [])
            ][:40],
        )
    source_health = domain_runtime.shape_health(source)
    if not source_health.get("valid_non_null"):
        return _invalid("The array source does not have a valid native shape.", source=source_health)
    layout = _layout_preflight(array)
    if not layout.get("ok"):
        return _invalid(
            "The requested array contains coincident or rank-deficient instance positions; no array was created.",
            layout=layout,
        )
    if kind == "orthogonal":
        if int(array["count_x"]) * int(array["count_y"]) < 2:
            return _invalid(
                "An orthogonal array needs at least 2 total copies; "
                "increase count_x or count_y."
            )

    def create() -> dict[str, Any]:
        import Draft
        import FreeCAD as App
        from draftutils import timeline

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        base = active.getObject(source_name)
        if base is None:
            raise RuntimeError("The source object no longer exists.")
        if kind == "orthogonal":
            obj = Draft.make_ortho_array(
                base,
                v_x=domain_runtime.parse_vector(array["interval_x"]),
                v_y=domain_runtime.parse_vector(array["interval_y"]),
                v_z=App.Vector(0, 0, 0),
                n_x=int(array["count_x"]),
                n_y=int(array["count_y"]),
                n_z=1,
            )
        else:
            obj = Draft.make_polar_array(
                base,
                number=int(array["count"]),
                angle=float(array["total_angle_degrees"]),
                center=domain_runtime.parse_vector(array["center"]),
            )
        if obj is None:
            raise RuntimeError("Draft array creation did not return an object.")
        timeline.accept_derived_output(obj, [base])
        obj.Label = clean_label
        active.recompute()
        return {
            "document": active.Name,
            "feature": obj.Name,
            "feature_label": obj.Label,
            "feature_type": obj.TypeId,
            "array_type": kind,
            "source_object": base.Name,
            "source_shape": source_health,
            "layout_preflight": layout,
            "requested_instance_count": int(layout["requested_instance_count"]),
            "actual_instance_count": _native_instance_count(obj, kind),
            "shape_child_count": _child_shape_count(getattr(obj, "Shape", None)),
            "native_source_link": getattr(getattr(obj, "Base", None), "Name", None),
            "source_relationships": {
                "in_list": [item.Name for item in list(getattr(base, "InList", []) or [])],
                "out_list": [item.Name for item in list(getattr(base, "OutList", []) or [])],
            },
            "shape": domain_runtime.shape_summary(obj),
            "feature_state": domain_runtime.feature_state_summary(obj),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        expected = int(result.get("requested_instance_count", 0))
        actual = result.get("actual_instance_count")
        checks = [
            {
                "name": "source_link",
                "ok": result.get("native_source_link") == source_name,
                "expected": source_name,
                "actual": result.get("native_source_link"),
            },
            {
                "name": "instance_count",
                "ok": isinstance(actual, int) and actual == expected,
                "expected": expected,
                "actual": actual,
            },
            {
                "name": "nonempty_shape",
                "ok": bool((result.get("shape") or {}).get("available"))
                and int((result.get("shape") or {}).get("edges", 0)) > 0,
                "actual": result.get("shape"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create Draft {kind} array: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(
        transaction, operation=f"create_{kind}_array"
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _layout_preflight(array: dict[str, Any]) -> dict[str, Any]:
    kind = str(array.get("type") or "")
    if kind == "orthogonal":
        first = domain_runtime.parse_vector(array["interval_x"])
        second = domain_runtime.parse_vector(array["interval_y"])
        count_x = int(array["count_x"])
        count_y = int(array["count_y"])
        first_required = count_x > 1
        second_required = count_y > 1
        first_zero = float(first.Length) <= 1.0e-9
        second_zero = float(second.Length) <= 1.0e-9
        cross = first.cross(second)
        rank = (
            0
            if (not first_required or first_zero) and (not second_required or second_zero)
            else 1
            if not first_required or not second_required or float(cross.Length) <= 1.0e-9
            else 2
        )
        positions = [first * x + second * y for x in range(count_x) for y in range(count_y)]
        collisions = _coincident_positions(positions)
        failures = []
        if first_required and first_zero:
            failures.append("interval_x_is_zero_with_multiple_copies")
        if second_required and second_zero:
            failures.append("interval_y_is_zero_with_multiple_copies")
        if first_required and second_required and rank < 2:
            failures.append("grid_intervals_are_collinear")
        if collisions:
            failures.append("coincident_instance_positions")
        return {
            "ok": not failures,
            "type": kind,
            "interval_rank": rank,
            "interval_x": domain_runtime.vector_values(first),
            "interval_y": domain_runtime.vector_values(second),
            "requested_instance_count": count_x * count_y,
            "coincident_instances": collisions,
            "failures": failures,
        }
    center = domain_runtime.parse_vector(array["center"])
    count = int(array["count"])
    angle = float(array["total_angle_degrees"])
    return {
        "ok": count >= 2 and 0.0 < angle <= 360.0,
        "type": kind,
        "center": domain_runtime.vector_values(center),
        "requested_instance_count": count,
        "total_angle_degrees": angle,
        "coincident_instances": [],
        "failures": [],
    }


def _coincident_positions(positions: list[Any]) -> list[dict[str, Any]]:
    collisions = []
    for first in range(len(positions)):
        for second in range(first + 1, len(positions)):
            distance = float((positions[second] - positions[first]).Length)
            if distance <= 1.0e-9:
                collisions.append(
                    {"first_index": first, "second_index": second, "distance_mm": distance}
                )
    return collisions


def _native_instance_count(obj: Any, kind: str) -> int:
    try:
        if kind == "orthogonal":
            return int(obj.NumberX) * int(obj.NumberY) * int(obj.NumberZ)
        return int(obj.NumberPolar)
    except Exception as exc:
        raise RuntimeError(
            f"FreeCAD could not read the native {kind} array instance count: {exc}"
        ) from exc


def _child_shape_count(shape: Any) -> int:
    if shape is None or bool(shape.isNull()):
        return 0
    try:
        return len(list(shape.childShapes()))
    except Exception as exc:
        raise RuntimeError(
            f"FreeCAD could not enumerate the array result's child shapes: {exc}"
        ) from exc
