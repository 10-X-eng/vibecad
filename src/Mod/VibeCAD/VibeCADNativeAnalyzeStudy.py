# SPDX-License-Identifier: LGPL-2.1-or-later

"""Persistent, composable intent for one FEM analysis."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeOwnership import is_study, studies_in_document


STUDY_SCHEMA_VERSION = 1
STUDY_PHYSICS = ("mechanical", "thermal", "fluid", "electromagnetic")
STUDY_REGIMES = ("steady", "transient", "modal")
STUDY_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "physics": {
            "type": "array",
            "items": {"type": "string", "enum": list(STUDY_PHYSICS)},
            "minItems": 1,
            "maxItems": len(STUDY_PHYSICS),
            "uniqueItems": True,
            "description": "Domains explicitly solved by this study.",
        },
        "regime": {"type": "string", "enum": list(STUDY_REGIMES)},
    },
    "required": ["physics", "regime"],
    "additionalProperties": False,
}

_PHYSICS_PROPERTY = "StudyPhysics"
_REGIME_PROPERTY = "StudyRegime"
_VERSION_PROPERTY = "StudySchemaVersion"
_DEPENDENCIES_PROPERTY = "StudyDependencies"
_PROPERTY_GROUP = "Study"


def normalize_study_intent(value: Any) -> tuple[tuple[str, ...], str]:
    if not isinstance(value, Mapping) or set(value) != {"physics", "regime"}:
        raise NativeAnalyzeError("study must contain only physics and regime.")
    raw_physics = value["physics"]
    if (
        isinstance(raw_physics, (str, bytes, bytearray))
        or not isinstance(raw_physics, (list, tuple))
        or not 1 <= len(raw_physics) <= len(STUDY_PHYSICS)
    ):
        raise NativeAnalyzeError("physics must contain one to four supported values.")
    physics = tuple(str(item or "") for item in raw_physics)
    if any(item not in STUDY_PHYSICS for item in physics):
        raise NativeAnalyzeError(
            "physics values must be mechanical, thermal, fluid, or electromagnetic."
        )
    if len(set(physics)) != len(physics):
        raise NativeAnalyzeError("physics values must be unique.")
    regime = str(value["regime"] or "")
    if regime not in STUDY_REGIMES:
        raise NativeAnalyzeError("regime must be steady, transient, or modal.")
    return physics, regime


def _ensure_property(analysis: Any, property_type: str, name: str, description: str) -> None:
    properties = set(getattr(analysis, "PropertiesList", ()) or ())
    if name not in properties:
        analysis.addProperty(property_type, name, _PROPERTY_GROUP, description)
        return
    if str(analysis.getTypeIdOfProperty(name)) != property_type:
        raise NativeAnalyzeError(
            f"Existing analysis property {name} has an incompatible type."
        )


def configure_study_intent(analysis: Any, value: Any) -> dict[str, Any]:
    physics, regime = normalize_study_intent(value)
    _ensure_property(
        analysis,
        "App::PropertyStringList",
        _PHYSICS_PROPERTY,
        "Physical domains solved by this study.",
    )
    _ensure_property(
        analysis,
        "App::PropertyString",
        _REGIME_PROPERTY,
        "Time behavior of this study.",
    )
    _ensure_property(
        analysis,
        "App::PropertyInteger",
        _VERSION_PROPERTY,
        "Study intent schema version.",
    )
    setattr(analysis, _PHYSICS_PROPERTY, list(physics))
    setattr(analysis, _REGIME_PROPERTY, regime)
    setattr(analysis, _VERSION_PROPERTY, STUDY_SCHEMA_VERSION)
    return study_intent_state(analysis)


def study_intent_state(analysis: Any) -> dict[str, Any]:
    properties = set(getattr(analysis, "PropertiesList", ()) or ())
    names = {_PHYSICS_PROPERTY, _REGIME_PROPERTY, _VERSION_PROPERTY}
    if not (properties & names):
        return {"declared": False}
    if not names <= properties:
        raise NativeAnalyzeError("The analysis has an incomplete study declaration.")
    physics, regime = normalize_study_intent(
        {
            "physics": list(getattr(analysis, _PHYSICS_PROPERTY)),
            "regime": str(getattr(analysis, _REGIME_PROPERTY)),
        }
    )
    version = int(getattr(analysis, _VERSION_PROPERTY))
    if version != STUDY_SCHEMA_VERSION:
        raise NativeAnalyzeError(
            f"The analysis uses unsupported study schema version {version}."
        )
    result = {
        "declared": True,
        "physics": list(physics),
        "regime": regime,
        "schema_version": version,
    }
    result["state_sha256"] = hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return result


def _study_dependencies(analysis: Any) -> tuple[Any, ...]:
    properties = set(getattr(analysis, "PropertiesList", ()) or ())
    if _DEPENDENCIES_PROPERTY not in properties:
        return ()
    if str(analysis.getTypeIdOfProperty(_DEPENDENCIES_PROPERTY)) != "App::PropertyLinkList":
        raise NativeAnalyzeError(
            "Existing analysis property StudyDependencies has an incompatible type."
        )
    document = getattr(analysis, "Document", None)
    dependencies = tuple(getattr(analysis, _DEPENDENCIES_PROPERTY, ()) or ())
    if (
        len({id(value) for value in dependencies}) != len(dependencies)
        or any(not is_study(value, document) for value in dependencies)
        or analysis in dependencies
    ):
        raise NativeAnalyzeError("The FEM study dependency graph is invalid.")
    return dependencies


def _validate_dependency_graph(
    analysis: Any,
    dependencies: tuple[Any, ...],
) -> None:
    document = getattr(analysis, "Document", None)
    studies = studies_in_document(document)
    if analysis not in studies:
        raise NativeAnalyzeError("The FEM study is no longer live in its document.")
    complete: set[int] = set()
    visiting: set[int] = set()

    def visit(study: Any) -> None:
        identity = id(study)
        if identity in visiting:
            raise NativeAnalyzeError(
                "The FEM study dependency graph contains a cycle.",
                error_code="NATIVE_ANALYZE_DEPENDENCY_CYCLE",
            )
        if identity in complete:
            return
        visiting.add(identity)
        children = dependencies if study is analysis else _study_dependencies(study)
        for child in children:
            visit(child)
        visiting.remove(identity)
        complete.add(identity)

    for study in studies:
        visit(study)


def configure_study_dependencies(
    analysis: Any,
    dependencies: Any,
) -> dict[str, Any]:
    """Persist an exact directed acyclic list of prerequisite studies."""

    if isinstance(dependencies, (str, bytes, bytearray)) or not isinstance(
        dependencies, (list, tuple)
    ):
        raise NativeAnalyzeError("depends_on must be an array of FEM studies.")
    values = tuple(dependencies)
    document = getattr(analysis, "Document", None)
    if (
        len(values) > 64
        or len({id(value) for value in values}) != len(values)
        or any(not is_study(value, document) for value in values)
        or analysis in values
    ):
        raise NativeAnalyzeError(
            "depends_on must contain up to 64 unique studies from the same document."
        )
    _validate_dependency_graph(analysis, values)
    _ensure_property(
        analysis,
        "App::PropertyLinkList",
        _DEPENDENCIES_PROPERTY,
        "Studies whose results precede this study.",
    )
    setattr(analysis, _DEPENDENCIES_PROPERTY, list(values))
    return study_dependency_state(analysis)


def study_dependency_state(analysis: Any) -> dict[str, Any]:
    """Return explicit prerequisite study names; absence means an independent root."""

    return {
        "depends_on": [str(value.Name) for value in _study_dependencies(analysis)]
    }


def solver_configuration_blockers(
    intent: Mapping[str, Any],
    solver_states: list[Mapping[str, Any]],
) -> list[str]:
    """Return exact solver settings that conflict with declared study intent."""

    if intent.get("declared") is not True:
        return []
    physics = set(intent.get("physics") or ())
    regime = str(intent.get("regime") or "")
    blockers = []
    for state in solver_states:
        if state.get("suppressed") is True or state.get("solver_kind") != "calculix":
            continue
        settings = state.get("settings")
        settings = settings if isinstance(settings, Mapping) else {}
        if "thermal" not in physics:
            continue
        if str(settings.get("AnalysisType") or "").casefold() != "thermomech":
            blockers.append("calculix_requires_thermomech")
        thermal_mode = str(settings.get("ThermoMechType") or "").casefold()
        if physics == {"thermal"} and thermal_mode != "pure heat transfer":
            blockers.append("calculix_requires_pure_heat_transfer")
        elif "mechanical" in physics and thermal_mode not in {"coupled", "uncoupled"}:
            blockers.append("calculix_requires_thermomechanical_mode")
        steady = settings.get("ThermoMechSteadyState")
        if regime == "steady" and steady is not True:
            blockers.append("calculix_requires_steady_thermal")
        elif regime == "transient" and steady is not False:
            blockers.append("calculix_requires_transient_thermal")
    return list(dict.fromkeys(blockers))


def evaluate_study_readiness(
    intent: Mapping[str, Any],
    inventory: Mapping[str, Any],
    runtimes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate declared engineering requirements from exact study inventory."""

    blockers: list[str] = []
    physics = tuple(intent.get("physics") or ()) if intent.get("declared") else ()
    regime = str(intent.get("regime") or "")
    solver_kinds = tuple(dict.fromkeys(inventory.get("solver_kinds") or ()))
    elmer_only = set(solver_kinds) == {"elmer"}

    if not physics:
        blockers.append("missing_study_intent")
    if int(inventory.get("geometry_source_count", 0) or 0) < 1:
        blockers.append("missing_geometry")

    if "mechanical" in physics:
        if int(inventory.get("mechanical_material_count", 0) or 0) < 1:
            blockers.append("missing_mechanical_material")
        if regime == "steady":
            if int(inventory.get("support_count", 0) or 0) < 1:
                blockers.append("missing_support")
            if int(inventory.get("load_count", 0) or 0) < 1:
                blockers.append("missing_mechanical_load")
        if elmer_only and not {
            "elasticity",
            "deformation",
        }.intersection(set(inventory.get("equation_kinds") or ())):
            blockers.append("missing_mechanical_equation")

    if "thermal" in physics:
        material_key = (
            "transient_thermal_material_count"
            if regime == "transient"
            else "thermal_material_count"
        )
        if int(inventory.get(material_key, 0) or 0) < 1:
            blockers.append("missing_thermal_material")
        thermal_families = set(inventory.get("thermal_condition_families") or ())
        if int(inventory.get("thermal_condition_count", 0) or 0) < 1:
            blockers.append("missing_thermal_condition")
        elif (
            regime == "transient"
            or "calculix" in set(inventory.get("solver_kinds") or ())
        ) and "initial_temperature" not in thermal_families:
            blockers.append("missing_initial_temperature")
        if elmer_only and "heat" not in set(
            inventory.get("equation_kinds") or ()
        ):
            blockers.append("missing_heat_equation")

    if "fluid" in physics:
        if int(inventory.get("fluid_material_count", 0) or 0) < 1:
            blockers.append("missing_fluid_material")
        fluid_kinds = set(inventory.get("fluid_constraint_kinds") or ())
        if "fluid_boundary" not in fluid_kinds:
            blockers.append("missing_fluid_boundary")
        if regime == "transient" and not fluid_kinds.intersection(
            {"initial_flow_velocity", "initial_pressure"}
        ):
            blockers.append("missing_initial_fluid_state")
        if elmer_only and "flow" not in set(
            inventory.get("equation_kinds") or ()
        ):
            blockers.append("missing_flow_equation")

    if "electromagnetic" in physics:
        electromagnetic_equations = {
            "electrostatic",
            "electric_force",
            "magnetodynamic",
            "magnetodynamic_2d",
            "static_current",
        }
        if not electromagnetic_equations.intersection(
            set(inventory.get("equation_kinds") or ())
        ):
            blockers.append("missing_electromagnetic_equation")
        if int(inventory.get("electromagnetic_constraint_count", 0) or 0) < 1:
            blockers.append("missing_electromagnetic_constraint")

    if int(inventory.get("assignment_validation_issue_count", 0) or 0) > 0:
        blockers.append("invalid_assignments")
    if int(inventory.get("mesh_coverage_issue_count", 0) or 0) > 0:
        blockers.append("invalid_mesh_coverage")

    mesh_blockers = []
    if int(inventory.get("mesh_definition_count", 0) or 0) < 1:
        mesh_blockers.append("missing_mesh_definition")
    if int(inventory.get("generated_mesh_count", 0) or 0) < 1:
        mesh_blockers.append("missing_generated_mesh")
    blockers.extend(mesh_blockers)

    if not solver_kinds:
        blockers.append("missing_solver")
    else:
        raw_configurations = inventory.get("solver_configurations")
        configurations = (
            [
                value
                for value in raw_configurations
                if isinstance(value, Mapping)
                and str(value.get("solver_kind") or "") in solver_kinds
            ]
            if isinstance(raw_configurations, list)
            else []
        )
        if not configurations:
            shared_blockers = [
                blocker
                for blocker in inventory.get("solver_configuration_blockers", ())
                if isinstance(blocker, str)
            ]
            configurations = [
                {"solver_kind": solver, "blockers": shared_blockers}
                for solver in solver_kinds
            ]
        usable_solver = False
        solver_blockers: list[str] = []
        for configuration in configurations:
            solver = str(configuration.get("solver_kind") or "")
            configuration_blockers = [
                value
                for value in list(configuration.get("blockers") or ())
                if isinstance(value, str)
            ]
            if configuration_blockers:
                solver_blockers.extend(configuration_blockers)
                continue
            status = runtimes.get(solver)
            if status is not None and status.get("engine_ready") is True:
                usable_solver = True
                break
            solver_blockers.append(f"solver_runtime_unavailable:{solver}")
        if not usable_solver:
            blockers.extend(dict.fromkeys(solver_blockers))

    ready_to_mesh_blockers = {
        "missing_study_intent",
        "missing_geometry",
        "missing_mechanical_material",
        "missing_thermal_material",
        "missing_fluid_material",
        "missing_mesh_definition",
    }
    return {
        "ready_to_mesh": not any(item in ready_to_mesh_blockers for item in blockers),
        "ready_to_solve": not blockers,
        "blockers": blockers,
    }
