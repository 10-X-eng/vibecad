# SPDX-License-Identifier: LGPL-2.1-or-later

"""Repair defects on one exact mesh object with selected repair passes."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime
from .mesh_analyze import analyze_mesh


TOOL_SPEC = {
    "name": "mesh.repair",
    "description": (
        "Repair one exact mesh object by running the selected repair passes "
        "in a safe fixed order (orientation, duplicates, non-manifolds, "
        "degenerations, self-intersections, hole filling). Run mesh.analyze "
        "first and select only the repairs that its report justifies; the "
        "result includes a before/after defect comparison."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "MeshWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the mesh object (Mesh::Feature) "
                    "to repair, as returned by core.inspect scope='domain'."
                ),
            },
            "harmonize_normals": {
                "type": "boolean",
                "description": (
                    "true reorients inconsistently oriented facets so all "
                    "normals point the same way; false skips this pass."
                ),
            },
            "remove_duplicates": {
                "type": "boolean",
                "description": (
                    "true removes duplicated points and duplicated facets; "
                    "false skips this pass."
                ),
            },
            "remove_non_manifolds": {
                "type": "boolean",
                "description": (
                    "true removes non-manifold edges and points (edges shared "
                    "by more than two facets); false skips this pass."
                ),
            },
            "fix_degenerations": {
                "type": "boolean",
                "description": (
                    "true removes degenerated (zero-area) facets; false skips "
                    "this pass."
                ),
            },
            "fix_self_intersections": {
                "type": "boolean",
                "description": (
                    "true repairs facets that intersect each other; false "
                    "skips this pass."
                ),
            },
            "fill_holes_max_edges": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Fill boundary holes whose outline has at most this many "
                    "edges; 0 skips hole filling. Small values (3-12) close "
                    "pinholes without capping intentional openings."
                ),
            },
        },
        "required": [
            "object_name",
            "harmonize_normals",
            "remove_duplicates",
            "remove_non_manifolds",
            "fix_degenerations",
            "fix_self_intersections",
            "fill_holes_max_edges",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    object_name: str,
    harmonize_normals: bool,
    remove_duplicates: bool,
    remove_non_manifolds: bool,
    fix_degenerations: bool,
    fix_self_intersections: bool,
    fill_holes_max_edges: int,
) -> dict[str, Any]:
    clean_name = str(object_name or "").strip()
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {object_name}")
    if getattr(obj, "Mesh", None) is None:
        return _invalid(
            f"Object is not a mesh (no Mesh property): {clean_name}. Use "
            "core.inspect scope='domain' for mesh names."
        )
    max_hole_edges = int(fill_holes_max_edges)
    selected = (
        bool(harmonize_normals)
        or bool(remove_duplicates)
        or bool(remove_non_manifolds)
        or bool(fix_degenerations)
        or bool(fix_self_intersections)
        or max_hole_edges > 0
    )
    if not selected:
        return _invalid(
            "No repair selected. Enable at least one repair pass, or set "
            "fill_holes_max_edges above 0."
        )
    baseline = analyze_mesh(obj.Mesh)
    if not baseline.get("complete"):
        return _invalid(
            "Mesh repair requires a complete native defect baseline; no repair "
            "was attempted because one or more checks are unknown.",
            before=baseline,
            unknown_checks=baseline.get("unknown_checks"),
        )
    pass_specs = _selected_passes(
        harmonize_normals=bool(harmonize_normals),
        remove_duplicates=bool(remove_duplicates),
        remove_non_manifolds=bool(remove_non_manifolds),
        fix_degenerations=bool(fix_degenerations),
        fix_self_intersections=bool(fix_self_intersections),
        max_hole_edges=max_hole_edges,
    )
    unjustified = [
        {
            "pass": spec["name"],
            "target_checks": spec["target_checks"],
        }
        for spec in pass_specs
        if not any(_defect_value(baseline, name) for name in spec["target_checks"])
    ]
    if unjustified:
        return _invalid(
            "One or more selected repair passes have no matching defect in "
            "the complete baseline; no repair was attempted.",
            unjustified_passes=unjustified,
            before=baseline,
        )

    def repair() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target = active.getObject(clean_name)
        if target is None:
            raise RuntimeError("The mesh object no longer exists.")
        before = analyze_mesh(target.Mesh)
        mesh = target.Mesh.copy()
        pass_results: list[dict[str, Any]] = []
        failed = False
        for spec in pass_specs:
            pass_before = analyze_mesh(mesh)
            if not any(
                _defect_value(pass_before, name) for name in spec["target_checks"]
            ):
                pass_results.append(
                    {
                        "pass": spec["name"],
                        "status": "already_resolved_by_prior_pass",
                        "target_checks": spec["target_checks"],
                        "before": pass_before,
                        "after": pass_before,
                        "native_calls": [],
                    }
                )
                continue
            native_calls: list[dict[str, Any]] = []
            for method_name, arguments in spec["calls"]:
                try:
                    native_result = getattr(mesh, method_name)(*arguments)
                    native_calls.append(
                        {
                            "method": method_name,
                            "arguments": list(arguments),
                            "status": "completed",
                            "native_result": _native_result(native_result),
                        }
                    )
                except Exception as exc:
                    native_calls.append(
                        {
                            "method": method_name,
                            "arguments": list(arguments),
                            "status": "failed",
                            "native_error": str(exc),
                        }
                    )
                    failed = True
                    break
            # Retain the exact successful prefix even when a later native call
            # in this pass fails.
            target.Mesh = mesh
            active.recompute()
            pass_after = analyze_mesh(target.Mesh)
            pass_results.append(
                {
                    "pass": spec["name"],
                    "status": "failed" if failed else "completed",
                    "target_checks": spec["target_checks"],
                    "before": pass_before,
                    "after": pass_after,
                    "native_calls": native_calls,
                    "intended_defect_delta": _selected_defect_delta(
                        pass_before, pass_after, spec["target_checks"]
                    ),
                    "regressions": _analysis_regressions(pass_before, pass_after),
                }
            )
            if failed:
                break
        unattempted = [
            spec["name"] for spec in pass_specs[len(pass_results) :]
        ]
        after = analyze_mesh(target.Mesh)
        return {
            "document": active.Name,
            "object": target.Name,
            "object_label": target.Label,
            "before": before,
            "passes": pass_results,
            "unattempted_passes": unattempted,
            "after": after,
            "selected_defect_delta": _selected_defect_delta(
                before,
                after,
                sorted(
                    {
                        target_check
                        for spec in pass_specs
                        for target_check in spec["target_checks"]
                        if _defect_value(before, target_check)
                    }
                ),
            ),
            "regressions": _analysis_regressions(before, after),
            "readiness_verdict": after.get("verdict"),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        passes = list(result.get("passes") or [])
        after = result.get("after") or {}
        selected_delta = list(result.get("selected_defect_delta") or [])
        checks = [
            {
                "name": "all_native_passes_completed",
                "ok": len(passes) == len(pass_specs)
                and all(item.get("status") != "failed" for item in passes),
                "passes": passes,
                "unattempted_passes": result.get("unattempted_passes"),
            },
            {
                "name": "selected_defects_improved",
                "ok": bool(selected_delta)
                and all(item.get("improved") is True for item in selected_delta),
                "selected_defect_delta": selected_delta,
            },
            {
                "name": "no_regressions",
                "ok": not result.get("regressions"),
                "regressions": result.get("regressions"),
            },
            {
                "name": "post_analysis_complete_and_nonempty",
                "ok": after.get("complete") is True and after.get("nonempty") is True,
                "after": after,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Repair mesh: {clean_name}",
        repair,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction.get("result"), dict)
        else {}
    )
    after = result.get("after") if isinstance(result.get("after"), dict) else {}
    if after.get("verdict") == "ready":
        next_action = "The complete post-analysis reports the mesh ready."
    elif after.get("verdict") == "unknown":
        next_action = (
            "The post-repair verdict is unknown; inspect the failed checks and "
            "do not convert or export this mesh as ready."
        )
    else:
        next_action = (
            "The selected defect improved, but other known defects remain; "
            "inspect after.known_defects before the next repair."
        )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "mesh_repair", **result},
        next_action=next_action,
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _selected_passes(**options: Any) -> list[dict[str, Any]]:
    passes: list[dict[str, Any]] = []
    if options["harmonize_normals"]:
        passes.append(
            {
                "name": "harmonize_normals",
                "calls": [("harmonizeNormals", ())],
                "target_checks": [
                    "inconsistent_orientation",
                    "non_uniform_oriented_facets",
                ],
            }
        )
    if options["remove_duplicates"]:
        passes.append(
            {
                "name": "remove_duplicates",
                "calls": [
                    ("removeDuplicatedPoints", ()),
                    ("removeDuplicatedFacets", ()),
                ],
                "target_checks": [
                    "duplicated_point_indices",
                    "duplicated_facet_indices",
                ],
            }
        )
    if options["remove_non_manifolds"]:
        passes.append(
            {
                "name": "remove_non_manifolds",
                "calls": [
                    ("removeNonManifolds", ()),
                    ("removeNonManifoldPoints", ()),
                ],
                "target_checks": ["non_manifold_edges"],
            }
        )
    if options["fix_degenerations"]:
        passes.append(
            {
                "name": "fix_degenerations",
                "calls": [("fixDegenerations", ())],
                "target_checks": ["degenerated_facets"],
            }
        )
    if options["fix_self_intersections"]:
        passes.append(
            {
                "name": "fix_self_intersections",
                "calls": [("fixSelfIntersections", ())],
                "target_checks": ["self_intersections"],
            }
        )
    if int(options["max_hole_edges"]) > 0:
        passes.append(
            {
                "name": "fill_holes",
                "calls": [("fillupHoles", (int(options["max_hole_edges"]),))],
                "target_checks": ["open_edges"],
            }
        )
    return passes


def _defect_value(analysis: dict[str, Any], name: str) -> bool:
    check = (analysis.get("checks") or {}).get(name) or {}
    return check.get("status") == "known" and check.get("defect") is True


def _selected_defect_delta(
    before: dict[str, Any], after: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    before_checks = before.get("checks") or {}
    after_checks = after.get("checks") or {}
    for name in names:
        before_check = before_checks.get(name) or {}
        after_check = after_checks.get(name) or {}
        before_value = before_check.get("value")
        after_value = after_check.get("value")
        if isinstance(before_value, int) and not isinstance(before_value, bool):
            improved = (
                isinstance(after_value, int)
                and after_value < before_value
            )
        else:
            improved = (
                before_check.get("defect") is True
                and after_check.get("status") == "known"
                and after_check.get("defect") is False
            )
        result.append(
            {
                "check": name,
                "before": before_check,
                "after": after_check,
                "improved": improved,
                "resolved": after_check.get("defect") is False,
            }
        )
    return result


def _analysis_regressions(
    before: dict[str, Any], after: dict[str, Any]
) -> list[dict[str, Any]]:
    regressions: list[dict[str, Any]] = []
    before_checks = before.get("checks") or {}
    after_checks = after.get("checks") or {}
    for name, before_check in before_checks.items():
        after_check = after_checks.get(name) or {}
        if before_check.get("status") == "known" and after_check.get("status") != "known":
            regressions.append(
                {"check": name, "reason": "became_unknown", "after": after_check}
            )
        elif before_check.get("defect") is False and after_check.get("defect") is True:
            regressions.append(
                {"check": name, "reason": "new_defect", "after": after_check}
            )
    before_components = before.get("component_count")
    after_components = after.get("component_count")
    if (
        isinstance(before_components, int)
        and isinstance(after_components, int)
        and after_components > before_components
    ):
        regressions.append(
            {
                "check": "component_count",
                "reason": "component_count_increased",
                "before": before_components,
                "after": after_components,
            }
        )
    if before.get("nonempty") and not after.get("nonempty"):
        regressions.append({"check": "nonempty", "reason": "mesh_became_empty"})
    return regressions


def _native_result(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
