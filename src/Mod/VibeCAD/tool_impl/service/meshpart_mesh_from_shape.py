# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one mesh object by tessellating an exact shaped object."""

from __future__ import annotations

from math import radians
from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime
from .mesh_analyze import analyze_mesh


TOOL_SPEC = {
    "name": "meshpart.mesh_from_shape",
    "description": (
        "Create one mesh object (Mesh::Feature) by tessellating an exact "
        "shaped BREP object. The source object is unchanged and stays in the "
        "document; the mesh is a triangle snapshot suitable for export or "
        "mesh analysis, not for parametric modeling. Smaller deflection "
        "values give finer, heavier meshes."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "MeshPartWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "source_object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the shaped object (Part::Feature "
                    "or derived) to tessellate."
                ),
            },
            "linear_deflection_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Maximum allowed distance in mm between the mesh and the "
                    "exact surface; 0.1 suits most parts, smaller is finer."
                ),
            },
            "angular_deflection_degrees": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 90,
                "description": (
                    "Maximum allowed angle in degrees between adjacent mesh "
                    "facets on curved surfaces; 28.5 is the FreeCAD default, "
                    "smaller is finer."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new mesh object.",
            },
        },
        "required": [
            "source_object_name",
            "linear_deflection_mm",
            "angular_deflection_degrees",
            "label",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    source_object_name: str,
    linear_deflection_mm: float,
    angular_deflection_degrees: float,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    clean_name = str(source_object_name or "").strip()
    linear_deflection = float(linear_deflection_mm)
    angular_deflection = float(angular_deflection_degrees)
    if linear_deflection <= 0.0:
        return _invalid("linear_deflection_mm must be positive.")
    if angular_deflection <= 0.0 or angular_deflection > 90.0:
        return _invalid(
            "angular_deflection_degrees must be greater than 0 and no more than 90."
        )
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(
            f"Object not found by exact internal name: {source_object_name}"
        )
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(
            f"Object has no shape geometry to tessellate: {clean_name}. "
            "meshpart.mesh_from_shape needs a shaped BREP object."
        )
    source_health = domain_runtime.shape_health(obj)
    if not source_health.get("valid_non_null"):
        return _invalid(
            "The source BREP is not a valid non-null shape; tessellation was not attempted.",
            source=source_health,
        )
    try:
        import MeshPart
    except ImportError:
        return _invalid(
            "The MeshPart module is not available in this FreeCAD build; "
            "shapes cannot be tessellated."
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        source = active.getObject(clean_name)
        if source is None:
            raise RuntimeError("The source object no longer exists.")
        mesh = MeshPart.meshFromShape(
            Shape=source.Shape,
            LinearDeflection=linear_deflection,
            AngularDeflection=radians(angular_deflection),
            Relative=False,
        )
        feature = active.addObject("Mesh::Feature", "Mesh")
        feature.Label = clean_label
        feature.Mesh = mesh
        active.recompute()
        return {
            "document": active.Name,
            "object": feature.Name,
            "object_label": feature.Label,
            "source_object": source.Name,
            "source_shape": source_health,
            "deviation_settings": {
                "linear_deflection_mm": linear_deflection,
                "angular_deflection_degrees": angular_deflection,
                "angular_deflection_radians_used": radians(angular_deflection),
                "relative": False,
            },
            "mesh": analyze_mesh(feature.Mesh),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        mesh = result.get("mesh") or {}
        source_bounds = (
            ((result.get("source_shape") or {}).get("shape") or {}).get("bound_box")
            or {}
        )
        mesh_bounds = mesh.get("bounds_mm") or {}
        checks = [
            {
                "name": "nonempty_mesh",
                "ok": mesh.get("nonempty") is True
                and int((mesh.get("counts") or {}).get("points", 0)) > 0
                and int((mesh.get("counts") or {}).get("facets", 0)) > 0,
                "counts": mesh.get("counts"),
            },
            {
                "name": "analysis_complete",
                "ok": mesh.get("complete") is True,
                "unknown_checks": mesh.get("unknown_checks"),
            },
            {
                "name": "bounds_match_source",
                "ok": _bounds_match(source_bounds, mesh_bounds, linear_deflection),
                "source_bounds_mm": source_bounds,
                "mesh_bounds_mm": mesh_bounds,
                "tolerance_mm": linear_deflection,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Mesh from shape: {clean_label}",
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
        extra={"operation": "mesh_from_shape", **result},
        next_action=(
            "Check the returned facet count: refine deflection if too coarse, "
            "or ask the human to inspect the result in the owning workbench."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _bounds_match(
    source_bounds: dict[str, Any], mesh_bounds: dict[str, Any], tolerance: float
) -> bool:
    if mesh_bounds.get("status") != "known":
        return False
    try:
        source_min = [
            float(source_bounds["xmin"]),
            float(source_bounds["ymin"]),
            float(source_bounds["zmin"]),
        ]
        source_max = [
            float(source_bounds["xmax"]),
            float(source_bounds["ymax"]),
            float(source_bounds["zmax"]),
        ]
        mesh_min = [float(value) for value in mesh_bounds["min"]]
        mesh_max = [float(value) for value in mesh_bounds["max"]]
    except (KeyError, TypeError, ValueError):
        return False
    allowed = max(float(tolerance), 1.0e-6)
    return all(
        abs(actual - expected) <= allowed
        for actual, expected in zip(mesh_min + mesh_max, source_min + source_max)
    )
