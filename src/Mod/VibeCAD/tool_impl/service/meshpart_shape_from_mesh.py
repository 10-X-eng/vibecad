# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one BREP shape object from an exact mesh object."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime
from .mesh_analyze import analyze_mesh


TOOL_SPEC = {
    "name": "meshpart.shape_from_mesh",
    "description": (
        "Create one BREP shape object (Part::Feature) from an exact mesh "
        "object. The mesh is unchanged. Every mesh triangle becomes a planar "
        "face, so the result is faceted, not smooth; a watertight, defect-free "
        "source mesh is required for a valid solid."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "MeshPartWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "mesh_object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the mesh object (Mesh::Feature) "
                    "already present in the active document."
                ),
            },
            "sewing_tolerance_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Tolerance in mm for sewing neighbouring triangles into "
                    "a connected shell; 0.1 suits most meshes."
                ),
            },
            "make_solid": {
                "type": "boolean",
                "description": (
                    "true converts the sewn shell into a solid (requires a "
                    "watertight mesh); false keeps the result as a shell."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new shape object.",
            },
        },
        "required": [
            "mesh_object_name",
            "sewing_tolerance_mm",
            "make_solid",
            "label",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    mesh_object_name: str,
    sewing_tolerance_mm: float,
    make_solid: bool,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    clean_name = str(mesh_object_name or "").strip()
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {mesh_object_name}")
    mesh = getattr(obj, "Mesh", None)
    if mesh is None:
        return _invalid(
            f"Object is not a mesh (no Mesh property): {clean_name}. Provide "
            "the exact name of an existing Mesh::Feature."
        )
    tolerance = float(sewing_tolerance_mm)
    if tolerance <= 0.0:
        return _invalid("sewing_tolerance_mm must be positive.")
    mesh_analysis = analyze_mesh(mesh)
    if not mesh_analysis.get("complete"):
        return _invalid(
            "The mesh defect analysis is incomplete; conversion was not "
            "attempted because its topology cannot be trusted.",
            mesh_analysis=mesh_analysis,
            unknown_checks=mesh_analysis.get("unknown_checks"),
        )
    if mesh_analysis.get("known_defects"):
        return _invalid(
            "The mesh has known topology defects; conversion was not attempted.",
            mesh_analysis=mesh_analysis,
            known_defects=mesh_analysis.get("known_defects"),
        )
    if make_solid and mesh_analysis.get("is_solid_watertight") is not True:
        return _invalid(
            "make_solid requires a natively verified watertight mesh; conversion "
            "was not attempted.",
            mesh_analysis=mesh_analysis,
        )
    try:
        import Part  # noqa: F401
    except ImportError:
        return _invalid(
            "The Part module is not available in this FreeCAD build; meshes "
            "cannot be converted to shapes."
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App
        import Part

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        source = active.getObject(clean_name)
        if source is None:
            raise RuntimeError("The mesh object no longer exists.")
        stages: list[dict[str, Any]] = []
        shape = Part.Shape()
        failed_stage = None
        try:
            shape.makeShapeFromMesh(source.Mesh.Topology, tolerance)
            stages.append(
                {
                    "stage": "sewing",
                    "status": "completed",
                    "tolerance_mm": tolerance,
                    "shell_count": len(list(getattr(shape, "Shells", []) or [])),
                    "solid_count": len(list(getattr(shape, "Solids", []) or [])),
                    "closed": bool(shape.isClosed()),
                    "valid": bool(shape.isValid()),
                }
            )
        except Exception as exc:
            failed_stage = {
                "stage": "sewing",
                "status": "failed",
                "native_error": str(exc),
                "tolerance_mm": tolerance,
            }
            stages.append(failed_stage)
        if failed_stage is None and make_solid:
            sewn_shell_count = len(list(getattr(shape, "Shells", []) or []))
            if not shape.isClosed() or sewn_shell_count != 1:
                failed_stage = {
                    "stage": "solidification_precondition",
                    "status": "failed",
                    "reason": "sewn_shape_is_not_one_closed_shell",
                    "shell_count": sewn_shell_count,
                    "closed": bool(shape.isClosed()),
                }
                stages.append(failed_stage)
            else:
                try:
                    shape = Part.makeSolid(shape)
                    stages.append(
                        {
                            "stage": "solidification",
                            "status": "completed",
                            "shell_count": len(
                                list(getattr(shape, "Shells", []) or [])
                            ),
                            "solid_count": len(
                                list(getattr(shape, "Solids", []) or [])
                            ),
                            "closed": bool(shape.isClosed()),
                            "valid": bool(shape.isValid()),
                        }
                    )
                except Exception as exc:
                    failed_stage = {
                        "stage": "solidification",
                        "status": "failed",
                        "native_error": str(exc),
                    }
                    stages.append(failed_stage)
        feature = None
        if failed_stage is None:
            try:
                feature = active.addObject("Part::Feature", "ShapeFromMesh")
                feature.Label = clean_label
                feature.Shape = shape
                active.recompute()
                stages.append(
                    {
                        "stage": "document_feature",
                        "status": "completed",
                        "feature": feature.Name,
                    }
                )
            except Exception as exc:
                failed_stage = {
                    "stage": "document_feature",
                    "status": "failed",
                    "native_error": str(exc),
                    "retained_feature": getattr(feature, "Name", None),
                }
                stages.append(failed_stage)
        return {
            "document": active.Name,
            "feature": getattr(feature, "Name", None),
            "feature_label": getattr(feature, "Label", None),
            "feature_type": getattr(feature, "TypeId", None),
            "mesh_object": source.Name,
            "mesh_analysis": mesh_analysis,
            "requested_make_solid": bool(make_solid),
            "sewing_tolerance_mm": tolerance,
            "stages": stages,
            "failed_stage": failed_stage,
            "actual_shell_count": len(list(getattr(shape, "Shells", []) or [])),
            "actual_solid_count": len(list(getattr(shape, "Solids", []) or [])),
            "sewn_shape_closed": bool(shape.isClosed()) if not shape.isNull() else False,
            "shape_valid": bool(shape.isValid()) if not shape.isNull() else False,
            "shape": domain_runtime.shape_summary(feature) if feature else None,
            "feature_state": domain_runtime.feature_state_summary(feature)
            if feature
            else None,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        expected_solids = 1 if make_solid else 0
        checks = [
            {
                "name": "native_stages_completed",
                "ok": result.get("failed_stage") is None,
                "stages": result.get("stages"),
                "failed_stage": result.get("failed_stage"),
            },
            {
                "name": "shell_sewn_closed",
                "ok": result.get("sewn_shape_closed") is True,
                "actual_shell_count": result.get("actual_shell_count"),
                "closed": result.get("sewn_shape_closed"),
            },
            {
                "name": "requested_solid_count",
                "ok": int(result.get("actual_solid_count", 0)) == expected_solids,
                "requested_make_solid": bool(make_solid),
                "actual_solid_count": result.get("actual_solid_count"),
            },
            {
                "name": "valid_shape_and_feature",
                "ok": result.get("shape_valid") is True
                and bool(result.get("feature")),
                "shape": result.get("shape"),
                "feature_state": result.get("feature_state"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Shape from mesh: {clean_label}",
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
        extra={"operation": "shape_from_mesh", **result},
        next_action=(
            "The shape is faceted (one planar face per triangle); ask the "
            "human to inspect it before using large results downstream."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
