# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Draft rectangle at an exact global position."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "draft.create_rectangle",
    "description": (
        "Create one native Draft rectangle on the global XY plane with its "
        "lower-left corner at an exact position. With make_face=true the "
        "rectangle is a filled planar face that another workbench can consume "
        "later as a profile."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "DraftWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "corner": domain_runtime.vector_schema(
                "Exact global position of the rectangle's lower-left corner in mm."
            ),
            "length_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Rectangle size along the global X axis in mm.",
            },
            "height_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Rectangle size along the global Y axis in mm.",
            },
            "make_face": {
                "type": "boolean",
                "description": (
                    "True to fill the rectangle into a planar face usable as an "
                    "extrusion profile; false leaves an open wire outline."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new rectangle, e.g. 'Panel'.",
            },
        },
        "required": ["corner", "length_mm", "height_mm", "make_face", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    corner: dict[str, Any],
    length_mm: float,
    height_mm: float,
    make_face: bool,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    length = float(length_mm)
    height = float(height_mm)
    if length <= 0 or height <= 0:
        return _invalid("length_mm and height_mm must both be greater than 0.")

    def create() -> dict[str, Any]:
        import Draft
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        placement = App.Placement(domain_runtime.parse_vector(corner), App.Rotation())
        obj = Draft.make_rectangle(
            length,
            height,
            placement=placement,
            face=bool(make_face),
        )
        if obj is None:
            raise RuntimeError("Draft.make_rectangle did not create an object.")
        obj.Label = clean_label
        doc.recompute()
        return {
            "document": doc.Name,
            "feature": obj.Name,
            "feature_label": obj.Label,
            "feature_type": obj.TypeId,
            "length_mm": length,
            "height_mm": height,
            "shape": domain_runtime.shape_summary(obj),
            "feature_state": domain_runtime.feature_state_summary(obj),
        }

    transaction = run_freecad_transaction(
        f"Create Draft rectangle: {clean_label}",
        create,
    )
    return domain_runtime.part_feature_result(transaction, operation="create_rectangle")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
