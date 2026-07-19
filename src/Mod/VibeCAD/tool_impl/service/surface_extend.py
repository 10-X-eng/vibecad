# SPDX-License-Identifier: LGPL-2.1-or-later

"""Extend one exact surface face beyond its current boundary."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime, partdesign_dressup_feature


TOOL_SPEC = {
    "name": "surface.extend",
    "description": (
        "Create one native Surface extension that enlarges an exact named face "
        "beyond its current boundary by a percentage of its size in each "
        "parametric direction. Useful to make a surface big enough for a "
        "later operation. Use exact face names from the active Surface context; "
        "never guess them."
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
                    "Exact internal name of the object that owns the face."
                ),
            },
            "selection": partdesign_dressup_feature.selection_schema(
                allow_all_edges=False,
                face_only=True,
                required_count=1,
            ),
            "extend_u_percent": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "description": (
                    "How far to extend on each side in the surface U "
                    "direction, as a percent of the face size in U; 0 leaves "
                    "U unchanged."
                ),
            },
            "extend_v_percent": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "description": (
                    "How far to extend on each side in the surface V "
                    "direction, as a percent of the face size in V; 0 leaves "
                    "V unchanged."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the extended surface.",
            },
        },
        "required": [
            "object_name",
            "selection",
            "extend_u_percent",
            "extend_v_percent",
            "label",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    object_name: str,
    selection: dict[str, Any],
    extend_u_percent: float,
    extend_v_percent: float,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    extend_u = float(extend_u_percent)
    extend_v = float(extend_v_percent)
    if extend_u <= 0 and extend_v <= 0:
        return _invalid(
            "At least one of extend_u_percent or extend_v_percent must be positive."
        )
    clean_name = str(object_name or "").strip()
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {object_name}")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(f"Object has no shape geometry: {clean_name}")
    selection_state = partdesign_dressup_feature.resolve_selection(
        service,
        obj,
        selection,
        allow_all_edges=False,
        face_only=True,
    )
    if not selection_state.get("ok"):
        return selection_state
    names = list(selection_state.get("subelements") or [])
    if len(names) != 1:
        return _invalid(
            "Surface extension requires exactly one resolved face.",
            selection=selection_state,
        )
    clean_face = names[0]
    face_index = int(clean_face.removeprefix("Face"))
    source_face = shape.Faces[face_index - 1]
    source_parameter_range = _parameter_range(source_face)

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        base = active.getObject(clean_name)
        if base is None:
            raise RuntimeError("The object no longer exists.")
        extend = active.addObject("Surface::Extend", "SurfaceExtend")
        extend.Label = clean_label
        extend.Face = (base, [clean_face])
        extend.ExtendUSymetric = True
        extend.ExtendVSymetric = True
        extend.ExtendUNeg = extend_u / 100.0
        extend.ExtendUPos = extend_u / 100.0
        extend.ExtendVNeg = extend_v / 100.0
        extend.ExtendVPos = extend_v / 100.0
        active.recompute()
        return {
            "document": active.Name,
            "feature": extend.Name,
            "feature_label": extend.Label,
            "feature_type": extend.TypeId,
            "source_object": base.Name,
            "selection_request": dict(selection),
            "resolved_face": selection_state["resolved_geometry"][0],
            "source_parameter_range": source_parameter_range,
            "requested_extension_percent": {"u_each_side": extend_u, "v_each_side": extend_v},
            "actual_extension_properties": {
                "u_negative": float(extend.ExtendUNeg) * 100.0,
                "u_positive": float(extend.ExtendUPos) * 100.0,
                "v_negative": float(extend.ExtendVNeg) * 100.0,
                "v_positive": float(extend.ExtendVPos) * 100.0,
                "native_fraction": {
                    "u_negative": float(extend.ExtendUNeg),
                    "u_positive": float(extend.ExtendUPos),
                    "v_negative": float(extend.ExtendVNeg),
                    "v_positive": float(extend.ExtendVPos),
                },
            },
            "result_parameter_ranges": [
                _parameter_range(face)
                for face in list(getattr(extend.Shape, "Faces", []) or [])
            ],
            "shape": domain_runtime.shape_summary(extend),
            "feature_state": domain_runtime.feature_state_summary(extend),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        shape = result.get("shape") or {}
        state = result.get("feature_state") or {}
        actual = result.get("actual_extension_properties") or {}
        checks = [
            {
                "name": "extension_properties",
                "ok": all(
                    abs(float(actual.get(key, -1.0)) - expected) <= 1.0e-9
                    for key, expected in {
                        "u_negative": extend_u,
                        "u_positive": extend_u,
                        "v_negative": extend_v,
                        "v_positive": extend_v,
                    }.items()
                ),
                "actual": actual,
            },
            {
                "name": "extended_surface_created",
                "ok": int(shape.get("faces", 0)) > 0
                and state.get("shape_valid") is True
                and not state.get("marked_invalid"),
                "actual": shape,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Extend surface: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation="surface_extend")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _parameter_range(face: Any) -> dict[str, float]:
    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
        return {
            "u_min": float(u_min),
            "u_max": float(u_max),
            "v_min": float(v_min),
            "v_max": float(v_max),
        }
    except Exception as exc:
        raise RuntimeError(
            f"FreeCAD could not read the face parameter range: {exc}"
        ) from exc
