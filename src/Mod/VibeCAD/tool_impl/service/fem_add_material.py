# SPDX-License-Identifier: LGPL-2.1-or-later

"""Add one solid material from the material library to an exact FEM analysis."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_SUMMARY_PROPERTIES = (
    "Density",
    "YoungsModulus",
    "PoissonRatio",
    "ThermalConductivity",
    "ThermalExpansionCoefficient",
    "SpecificHeat",
)


TOOL_SPEC = {
    "name": "fem.add_material",
    "description": (
        "Add one solid material to an exact FEM analysis from the material "
        "library by exact UUID supplied in the active FEM context or by the human. "
        "The solver reads mechanical values (Young's modulus, Poisson "
        "ratio, density) from this material; a solve without a material "
        "fails. One material covering the whole model is the common case."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "FemWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "analysis_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the FEM analysis from core.inspect scope='domain'."
                ),
            },
            "material_uuid": {
                "type": "string",
                "description": (
                    "Exact UUID of a material card with mechanical "
                    "properties (e.g. a steel or aluminium alloy) for "
                    "structural analyses."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new material object.",
            },
        },
        "required": ["analysis_name", "material_uuid", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    analysis_name: str,
    material_uuid: str,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    clean_uuid = str(material_uuid or "").strip()
    if not clean_uuid:
        return _invalid("material_uuid is required.")
    analysis = service._get_fem_analysis(str(analysis_name or "").strip())
    if analysis is None:
        return _invalid(
            f"FEM analysis not found by exact internal name: {analysis_name}. "
            "Call core.inspect with scope='domain' for exact names."
        )
    try:
        import ObjectsFem
    except ImportError:
        return _invalid(
            "The FEM workbench is not available in this FreeCAD build; "
            "materials cannot be added."
        )
    try:
        import Materials
    except ImportError:
        return _invalid(
            "The Materials module is not available in this FreeCAD build; "
            "material cards cannot be read."
        )
    try:
        manager = Materials.MaterialManager()
        card = manager.getMaterial(clean_uuid)
    except Exception as exc:
        return _invalid(
            f"Material not found by UUID {clean_uuid}: {exc}. Provide an exact "
            "valid material-card UUID."
        )
    if card is None:
        return _invalid(
            f"Material not found by UUID: {clean_uuid}. Provide an exact valid "
            "material-card UUID."
        )
    solver = _analysis_solver(analysis)
    if solver is None:
        return _invalid(
            "The FEM analysis has no CalculiX solver, so material requirements "
            "cannot be determined."
        )
    analysis_type = str(getattr(solver, "AnalysisType", "") or "")
    card_properties = dict(card.Properties)
    required_properties = _required_properties(analysis_type, solver)
    property_readiness = _property_readiness(card_properties, required_properties)
    missing = [
        name for name, state in property_readiness.items() if state["status"] != "present"
    ]
    if missing:
        return _invalid(
            "The selected material card is missing physical properties required "
            "by this analysis type; no FEM material object was created.",
            analysis_type=analysis_type,
            material_uuid=clean_uuid,
            required_properties=required_properties,
            property_readiness=property_readiness,
            missing_required_properties=missing,
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target = active.getObject(analysis.Name)
        if target is None:
            raise RuntimeError("The analysis no longer exists.")
        material_obj = ObjectsFem.makeMaterialSolid(active, "FemMaterial")
        material_obj.Label = clean_label
        material_obj.Material = card_properties
        if not hasattr(material_obj, "UUID"):
            raise RuntimeError(
                "The installed FEM material object has no UUID property; the "
                "material identity cannot be persisted."
            )
        material_obj.UUID = clean_uuid
        target.addObject(material_obj)
        active.recompute()
        actual_properties = dict(material_obj.Material)
        group_members = [obj.Name for obj in list(target.Group or [])]
        return {
            "document": active.Name,
            "analysis": target.Name,
            "material_object": material_obj.Name,
            "material_object_label": material_obj.Label,
            "analysis_type": analysis_type,
            "required_properties": required_properties,
            "material_name": str(actual_properties.get("Name", "")),
            "requested_material_uuid": clean_uuid,
            "actual_material_uuid": str(material_obj.UUID),
            "actual_material_properties": {
                name: {
                    "status": "present" if name in actual_properties else "missing",
                    "value": str(actual_properties[name])
                    if name in actual_properties
                    else None,
                }
                for name in _SUMMARY_PROPERTIES
            },
            "analysis_group_members": group_members,
            "material_in_analysis": material_obj.Name in group_members,
            "material_state": list(getattr(material_obj, "State", []) or []),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        actual_properties = result.get("actual_material_properties") or {}
        checks = [
            {
                "name": "material_membership",
                "ok": result.get("material_in_analysis") is True,
                "analysis_group_members": result.get("analysis_group_members"),
            },
            {
                "name": "uuid_readback",
                "ok": str(result.get("actual_material_uuid") or "").lower()
                == clean_uuid.lower(),
                "requested": clean_uuid,
                "actual": result.get("actual_material_uuid"),
            },
            {
                "name": "required_physical_properties",
                "ok": all(
                    (actual_properties.get(name) or {}).get("status") == "present"
                    for name in required_properties
                ),
                "required": required_properties,
                "actual": actual_properties,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Add FEM material: {clean_label}",
        create,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    envelope = domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "add_material", **result},
        next_action=(
            "Add constraints with fem.add_constraint (at least one fixed "
            "support and one load for a static analysis)."
        ),
    )
    return envelope


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _analysis_solver(analysis: Any) -> Any:
    solvers = [
        member
        for member in list(getattr(analysis, "Group", []) or [])
        if "Solver" in str(getattr(member, "TypeId", ""))
    ]
    return solvers[0] if len(solvers) == 1 else None


def _required_properties(analysis_type: str, solver: Any) -> list[str]:
    if analysis_type == "frequency":
        return ["Density", "YoungsModulus", "PoissonRatio"]
    if analysis_type == "thermomech":
        required = [
            "YoungsModulus",
            "PoissonRatio",
            "ThermalConductivity",
            "ThermalExpansionCoefficient",
        ]
        if not bool(getattr(solver, "ThermoMechSteadyState", False)):
            required.extend(["Density", "SpecificHeat"])
        return required
    if analysis_type in {"static", "buckling", "check"}:
        return ["YoungsModulus", "PoissonRatio"]
    raise ValueError(f"Unsupported CalculiX analysis type: {analysis_type}")


def _property_readiness(
    properties: dict[str, Any], required: list[str]
) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "status": "present"
            if name in properties and str(properties[name]).strip()
            else "missing",
            "value": str(properties[name]) if name in properties else None,
        }
        for name in required
    }
