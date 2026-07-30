# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native FEM analysis container with a CalculiX solver."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_ANALYSIS_TYPES = ("static", "frequency", "thermomech", "check", "buckling")


TOOL_SPEC = {
    "name": "fem.create_analysis",
    "description": (
        "Create one native FEM analysis container (Fem::FemAnalysis) with a "
        "CalculiX solver configured for an exact analysis type. The analysis "
        "starts empty: add a material with fem.add_material, constraints "
        "with fem.add_constraint, and a mesh with fem.mesh_analysis before "
        "running fem.solve."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "FemWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Visible label for the new analysis container.",
            },
            "analysis_type": {
                "type": "string",
                "enum": list(_ANALYSIS_TYPES),
                "description": (
                    "Solver analysis type: 'static' for stress/displacement "
                    "under loads, 'frequency' for natural frequencies, "
                    "'thermomech' for coupled thermal-mechanical, 'check' to "
                    "validate the model without solving, 'buckling' for "
                    "linear buckling factors."
                ),
            },
        },
        "required": ["label", "analysis_type"],
        "additionalProperties": False,
    },
}


def run(service: Any, label: str, analysis_type: str) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    clean_type = str(analysis_type or "").strip()
    try:
        native_supported_types = _native_supported_analysis_types()
    except Exception as exc:
        return _invalid(
            "Could not read the installed CalculiX solver's native AnalysisType choices.",
            native_error=str(exc),
        )
    if clean_type not in native_supported_types:
        return _invalid(
            f"Unknown analysis_type: {analysis_type}. "
            f"Choose one of the installed solver's native values: "
            f"{', '.join(native_supported_types)}.",
            native_supported_analysis_types=native_supported_types,
        )
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    try:
        import ObjectsFem
    except ImportError:
        return _invalid(
            "The FEM workbench is not available in this FreeCAD build; "
            "analyses cannot be created."
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        with domain_runtime.NewTimelineOperation() as timeline:
            analysis = ObjectsFem.makeAnalysis(active, "Analysis")
            timeline.set_operation(analysis)
            analysis.Label = clean_label
            solver = ObjectsFem.makeSolverCalculiXCcxTools(
                active,
                "CalculiXCcxTools",
            )
            timeline.add_resource(solver)
            solver.AnalysisType = clean_type
            solver.WorkingDir = ""
            solver.SplitInputWriter = False
            analysis.addObject(solver)
        active.recompute()
        group_members = [obj.Name for obj in list(analysis.Group or [])]
        return {
            "document": active.Name,
            "analysis": analysis.Name,
            "analysis_label": analysis.Label,
            "solver": solver.Name,
            "solver_type": solver.TypeId,
            "native_supported_analysis_types": list(
                solver.getEnumerationsOfProperty("AnalysisType")
            ),
            "requested_analysis_type": clean_type,
            "actual_analysis_type": str(solver.AnalysisType),
            "actual_solver_properties": {
                "working_directory": str(solver.WorkingDir),
                "split_input_writer": bool(solver.SplitInputWriter),
            },
            "analysis_group_members": group_members,
            "solver_in_analysis": solver.Name in group_members,
            "solver_state": {
                "state": list(getattr(solver, "State", []) or []),
                "proxy_type": str(getattr(getattr(solver, "Proxy", None), "Type", "")),
            },
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        state = result.get("solver_state") or {}
        checks = [
            {
                "name": "solver_membership",
                "ok": result.get("solver_in_analysis") is True,
                "analysis_group_members": result.get("analysis_group_members"),
            },
            {
                "name": "analysis_type_readback",
                "ok": result.get("actual_analysis_type") == clean_type
                and clean_type
                in list(result.get("native_supported_analysis_types") or []),
                "requested": clean_type,
                "actual": result.get("actual_analysis_type"),
                "supported": result.get("native_supported_analysis_types"),
            },
            {
                "name": "solver_object_ready",
                "ok": not list(state.get("state") or [])
                or list(state.get("state") or []) == ["Up-to-date"],
                "actual": state,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create FEM analysis: {clean_label}",
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
        extra={"operation": "create_analysis", **result},
        next_action=(
            "Add a material with fem.add_material using an exact card UUID, "
            "then constraints with "
            "fem.add_constraint, then a mesh with fem.mesh_analysis."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _native_supported_analysis_types() -> tuple[str, ...]:
    from femobjects.solver_ccxtools import SolverCcxTools

    proxy = SolverCcxTools.__new__(SolverCcxTools)
    for prop in proxy._get_properties():
        if prop.name == "AnalysisType":
            values = tuple(str(value) for value in list(prop.value))
            if values:
                return values
    raise RuntimeError("AnalysisType is absent or has no native enumeration values.")
