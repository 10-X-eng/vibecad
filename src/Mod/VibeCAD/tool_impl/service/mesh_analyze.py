# SPDX-License-Identifier: LGPL-2.1-or-later

"""Analyze one exact mesh object for defects that block downstream use."""

from __future__ import annotations

from typing import Any


_BOOLEAN_CHECKS = (
    ("non_manifold_edges", "hasNonManifolds", True),
    ("self_intersections", "hasSelfIntersections", True),
    ("inconsistent_orientation", "hasNonUniformOrientedFacets", True),
    ("invalid_points", "hasInvalidPoints", True),
    ("invalid_neighbourhood", "hasInvalidNeighbourhood", True),
    ("points_out_of_range", "hasPointsOutOfRange", True),
    ("facets_out_of_range", "hasFacetsOutOfRange", True),
    ("corrupted_facets", "hasCorruptedFacets", True),
    ("points_on_edges", "hasPointsOnEdge", True),
    ("is_solid_watertight", "isSolid", False),
)

_COUNT_CHECKS = (
    ("component_count", "countComponents", lambda value: value != 1),
    ("duplicated_point_indices", "countDuplicatedPoints", lambda value: value > 0),
    ("duplicated_facet_indices", "countDuplicatedFacets", lambda value: value > 0),
    ("degenerated_facets", "countDegeneratedFacets", lambda value: value > 0),
    ("open_edges", "countOpenEdges", lambda value: value > 0),
    (
        "non_uniform_oriented_facets",
        "countNonUniformOrientedFacets",
        lambda value: value > 0,
    ),
)


TOOL_SPEC = {
    "name": "mesh.analyze",
    "description": (
        "Analyze one exact mesh object and report the defects that matter "
        "for repair and BREP conversion: watertightness (solid), "
        "non-manifold edges, self-intersections, inconsistent facet "
        "orientation, invalid points, and the number of separate "
        "components. Run this before mesh.repair to pick repairs and "
        "before any later conversion to confirm the mesh is sound."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "MeshWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the mesh object (Mesh::Feature) "
                    "to analyze, as returned by mesh.list_meshes."
                ),
            },
        },
        "required": ["object_name"],
        "additionalProperties": False,
    },
}


def analyze_mesh(mesh: Any) -> dict[str, Any]:
    """Defect and size summary of one Mesh kernel object.

    Shared with mesh.repair for its before/after comparison. Every required
    native check reports a value or its exact native error; unknown checks can
    never be interpreted as a clean mesh.
    """
    counts = {
        "points": int(getattr(mesh, "CountPoints", 0) or 0),
        "edges": int(getattr(mesh, "CountEdges", 0) or 0),
        "facets": int(getattr(mesh, "CountFacets", 0) or 0),
    }
    checks: dict[str, dict[str, Any]] = {}
    for key, method_name, defect_value in _BOOLEAN_CHECKS:
        try:
            value = bool(getattr(mesh, method_name)())
            checks[key] = {
                "status": "known",
                "value": value,
                "defect": value is defect_value,
                "native_method": method_name,
            }
        except Exception as exc:
            checks[key] = {
                "status": "error",
                "value": None,
                "defect": None,
                "native_method": method_name,
                "native_error": str(exc),
            }
    for key, method_name, is_defect in _COUNT_CHECKS:
        try:
            value = int(getattr(mesh, method_name)())
            checks[key] = {
                "status": "known",
                "value": value,
                "defect": bool(is_defect(value)),
                "native_method": method_name,
            }
        except Exception as exc:
            checks[key] = {
                "status": "error",
                "value": None,
                "defect": None,
                "native_method": method_name,
                "native_error": str(exc),
            }
    known_defects = [
        name for name, check in checks.items() if check.get("defect") is True
    ]
    unknown_checks = [
        name for name, check in checks.items() if check.get("status") != "known"
    ]
    nonempty = counts["points"] > 0 and counts["facets"] > 0
    complete = not unknown_checks
    if not complete:
        verdict = "unknown"
    elif not nonempty or known_defects:
        verdict = "not_ready"
    else:
        verdict = "ready"
    summary: dict[str, Any] = {
        "counts": counts,
        "nonempty": nonempty,
        "complete": complete,
        "checks": checks,
        "known_defects": known_defects,
        "unknown_checks": unknown_checks,
        "has_defects": None if not complete else bool(known_defects or not nonempty),
        "verdict": verdict,
        "is_solid_watertight": _known_value(checks, "is_solid_watertight"),
        "component_count": _known_value(checks, "component_count"),
        "bounds_mm": _mesh_bounds(mesh),
    }
    summary["measurements"] = {
        "area_mm2": _read_numeric(mesh, "Area"),
        "volume_mm3": _read_numeric(mesh, "Volume"),
    }
    return summary


def run(service: Any, object_name: str) -> dict[str, Any]:
    clean_name = str(object_name or "").strip()
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {object_name}")
    mesh = getattr(obj, "Mesh", None)
    if mesh is None:
        return _invalid(
            f"Object is not a mesh (no Mesh property): {clean_name}. Use "
            "mesh.list_meshes for exact mesh names; conversion from shaped "
            "objects belongs to its own workbench."
        )
    try:
        summary = analyze_mesh(mesh)
    except Exception as exc:
        return _invalid(f"Mesh analysis failed: {exc}")
    result: dict[str, Any] = {
        "ok": True,
        "document": doc.Name,
        "object": obj.Name,
        "object_label": obj.Label,
        **summary,
    }
    if summary.get("verdict") == "unknown":
        result["next_action"] = (
            "The mesh verdict is unknown because required native checks failed; "
            "inspect unknown_checks before any conversion or repair claim."
        )
    elif summary.get("has_defects"):
        result["next_action"] = (
            "Repair the reported defects with mesh.repair, then re-run "
            "mesh.analyze to confirm."
        )
    elif summary.get("is_solid_watertight") is False:
        result["next_action"] = (
            "The mesh has no listed defects but is not watertight; "
            "mesh.repair with fill_holes may close small openings."
        )
    return result


def _known_value(checks: dict[str, dict[str, Any]], name: str) -> Any:
    check = checks.get(name) or {}
    return check.get("value") if check.get("status") == "known" else None


def _read_numeric(obj: Any, property_name: str) -> dict[str, Any]:
    try:
        return {
            "status": "known",
            "value": round(float(getattr(obj, property_name)), 6),
        }
    except Exception as exc:
        return {"status": "error", "value": None, "native_error": str(exc)}


def _mesh_bounds(mesh: Any) -> dict[str, Any]:
    try:
        bounds = mesh.BoundBox
        return {
            "status": "known",
            "min": [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
            "max": [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
            "size": [float(bounds.XLength), float(bounds.YLength), float(bounds.ZLength)],
        }
    except Exception as exc:
        return {"status": "error", "native_error": str(exc)}


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
