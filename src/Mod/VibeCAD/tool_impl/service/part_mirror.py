# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Part mirrored copy of an exact shaped object."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "part.mirror",
    "description": (
        "Create one native Part mirror of an exact named shaped object across a "
        "global plane defined by a point and a normal. The source object stays "
        "visible and unchanged; the mirror is a new parametric object linked to it."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "source_object_name": {
                "type": "string",
                "description": "Exact internal name of the object to mirror.",
            },
            "plane_point": domain_runtime.vector_schema(
                "A global point on the mirror plane in mm."
            ),
            "plane_normal": domain_runtime.vector_schema(
                "Normal of the mirror plane; only the direction matters.",
                units=None,
            ),
            "label": {
                "type": "string",
                "description": "Visible label for the mirrored object.",
            },
        },
        "required": ["source_object_name", "plane_point", "plane_normal", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    source_object_name: str,
    plane_point: dict[str, Any],
    plane_normal: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    source_name = str(source_object_name or "").strip()
    doc = service._active_document()
    source = doc.getObject(source_name) if doc is not None and source_name else None
    if source is None:
        return _invalid(
            f"Source object not found by exact internal name: {source_object_name}"
        )
    shape = getattr(source, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(f"Source object has no shape geometry: {source_name}")

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        base = active.getObject(source_name)
        if base is None:
            raise RuntimeError("The source object no longer exists.")
        normal = domain_runtime.parse_vector(plane_normal)
        if float(normal.Length) <= 1e-9:
            raise RuntimeError("plane_normal must be a non-zero vector.")
        mirror = active.addObject("Part::Mirroring", "Mirror")
        mirror.Label = clean_label
        mirror.Source = base
        mirror.Base = domain_runtime.parse_vector(plane_point)
        mirror.Normal = normal
        domain_runtime.adopt_part_result(mirror)
        active.recompute()
        return {
            "document": active.Name,
            "feature": mirror.Name,
            "feature_label": mirror.Label,
            "feature_type": mirror.TypeId,
            "source_object": base.Name,
            "shape": domain_runtime.shape_summary(mirror),
            "feature_state": domain_runtime.feature_state_summary(mirror),
        }

    transaction = run_freecad_transaction(
        f"Create Part mirror: {clean_label}",
        create,
    )
    return domain_runtime.part_feature_result(transaction, operation="mirror")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
