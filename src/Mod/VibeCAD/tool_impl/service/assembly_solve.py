# SPDX-License-Identifier: LGPL-2.1-or-later

"""Run the native assembly solver and report the exact outcome."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "assembly.solve",
    "description": (
        "Run the native solver on one exact assembly, moving every unfixed "
        "component to satisfy the current joints, and report the solver "
        "verdict (solved, over-constrained, conflicting, no grounded "
        "component) plus the resulting component placements. Run this after "
        "editing joints or component placements outside the joint tools."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "assembly_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the assembly from assembly.list_structure."
                ),
            },
        },
        "required": ["assembly_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, assembly_name: str) -> dict[str, Any]:
    assembly = _find_assembly(service, assembly_name)
    if assembly is None:
        return _invalid(
            f"Assembly not found by exact internal name: {assembly_name}. "
            "Call assembly.list_structure for exact names."
        )
    joint_group = domain_runtime.assembly_joint_group(assembly)
    if joint_group is None:
        return _invalid(
            "The assembly has no native Assembly::JointGroup and cannot be solved coherently.",
            assembly=assembly.Name,
            children=[
                {"name": child.Name, "label": child.Label, "type": child.TypeId}
                for child in list(getattr(assembly, "Group", []) or [])
            ],
        )
    components = _components(assembly)
    placements_before = {
        child.Name: {
            "assembly_local": domain_runtime.placement_summary(child),
            "global": domain_runtime.global_placement_summary(child),
        }
        for child in components
    }

    def solve() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target_assembly = active.getObject(assembly.Name)
        if target_assembly is None:
            raise RuntimeError("The assembly no longer exists.")
        solver_code = int(target_assembly.solve(False))
        active.recompute()
        native_components = _components(target_assembly)
        placements = {
            child.Name: {
                "assembly_local": domain_runtime.placement_summary(child),
                "global": domain_runtime.global_placement_summary(child),
            }
            for child in native_components
        }
        diagnostics = domain_runtime.assembly_solver_diagnostics(target_assembly)
        return {
            "document": active.Name,
            "assembly": target_assembly.Name,
            "solver_code": solver_code,
            "solver_verdict": domain_runtime.assembly_solver_verdict(solver_code),
            "solver_diagnostics": diagnostics,
            "component_placements_before": placements_before,
            "component_placements_after": placements,
            "component_placement_deltas": _placement_deltas(placements_before, placements),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        diagnostics = result.get("solver_diagnostics") or {}
        checks = [
            {
                "name": "native_solver_diagnostics",
                "ok": diagnostics.get("available") is True,
                "actual": diagnostics,
            },
            {
                "name": "solver_result",
                "ok": int(result.get("solver_code", -1)) == 0
                and not diagnostics.get("has_conflicts")
                and not diagnostics.get("has_redundancies")
                and not diagnostics.get("has_partial_redundancies")
                and not diagnostics.get("has_malformed_constraints"),
                "solver_code": result.get("solver_code"),
                "diagnostics": diagnostics,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Solve assembly: {assembly.Name}",
        solve,
        verifier=verify,
    )
    mutation = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    envelope = domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "solve", "mutation": mutation},
        next_action=(
            "Verify the returned component placements or capture a screenshot; "
            "if the verdict is not 'solved', fix the reported joint problem "
            "before adding more joints."
        ),
    )
    return envelope


def _find_assembly(service: Any, assembly_name: str) -> Any:
    clean = str(assembly_name or "").strip()
    if not clean:
        return None
    for assembly in service._assembly_objects():
        if assembly.Name == clean:
            return assembly
    return None


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _components(assembly: Any) -> list[Any]:
    return [
        child
        for child in list(getattr(assembly, "Group", []) or [])
        if str(getattr(child, "TypeId", "")) in {"App::Link", "Assembly::AssemblyLink"}
    ]


def _placement_deltas(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    deltas = {}
    for name in sorted(set(before).intersection(after)):
        first = ((before[name].get("global") or {}).get("placement") or {})
        second = ((after[name].get("global") or {}).get("placement") or {})
        first_position = first.get("position") or {}
        second_position = second.get("position") or {}
        if not first_position or not second_position:
            deltas[name] = {"available": False}
            continue
        dx = float(second_position["x"]) - float(first_position["x"])
        dy = float(second_position["y"]) - float(first_position["y"])
        dz = float(second_position["z"]) - float(first_position["z"])
        deltas[name] = {
            "available": True,
            "translation": {"x": dx, "y": dy, "z": dz},
            "translation_magnitude_mm": (dx * dx + dy * dy + dz * dz) ** 0.5,
            "rotation_angle_delta_degrees": float(second.get("rotation_angle_degrees", 0.0))
            - float(first.get("rotation_angle_degrees", 0.0)),
        }
    return deltas
