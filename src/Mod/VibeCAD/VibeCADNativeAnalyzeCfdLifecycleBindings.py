# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind focused CFD creation tools to shared Analyze runtimes."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeCfdLifecycleSchema import (
    ANALYZE_FLUID_MATERIAL,
    ANALYZE_OPENFOAM_SOLVER,
)
from VibeCADNativeAnalyzeCurrentTargets import (
    current_analysis_target,
    current_state,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeModelRuntime import NativeAnalyzeModelRuntime
from VibeCADNativeAnalyzeState import analysis_state, material_kind, material_state
from VibeCADNativeAnalyzeSolverRuntime import NativeAnalyzeSolverRuntime
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeMeshState import mesh_object_state


def _arguments(
    call: Any,
    runtime_type: type,
    operation: str,
) -> tuple[Any, dict[str, Any]]:
    runtime = getattr(call, "runtime", None)
    values = getattr(call, "arguments", None)
    if not isinstance(runtime, runtime_type):
        raise TypeError("A focused CFD call requires its exact runtime.")
    if not isinstance(values, Mapping):
        raise TypeError("A focused CFD call requires argument data.")
    result = dict(values)
    if result.pop("operation", None) != operation:
        raise ValueError(f"A focused CFD call requires the {operation} operation.")
    return runtime, result


def _material(call: Any) -> Mapping[str, Any]:
    arguments = getattr(call, "arguments", None)
    operation = (
        str(arguments.get("operation") or "")
        if isinstance(arguments, Mapping)
        else ""
    )
    if operation == "update":
        runtime, values = _arguments(call, NativeAnalyzeModelRuntime, "update")
        _material_object, target_state = current_state(
            runtime,
            values.pop("material_name"),
            material_state,
        )
        if target_state.get("material_kind") != "fluid":
            raise NativeAnalyzeError("The named material is not fluid.")
        target = {
            "object_name": target_state["object_name"],
            "expected_state_sha256": target_state["state_sha256"],
        }
        properties = {}
        for name in (
            "name",
            "density_kg_m3",
            "kinematic_viscosity_m2_s",
        ):
            if name in values:
                properties[name] = values.pop(name)
        request = {"operation": "update_material", "target": target}
        if properties:
            request["properties"] = properties
        if "label" in values:
            request["label"] = values.pop("label")
        if len(request) == 2:
            raise NativeAnalyzeError("Edit at least one fluid property or its label.")
        result = dict(
            runtime.execute(
                request,
                ticket=getattr(call, "ticket", None),
            )
        )
        result["material_name"] = str(target["object_name"])
        return result
    runtime, values = _arguments(call, NativeAnalyzeModelRuntime, "create")
    _source, source_state = current_state(
        runtime,
        values.pop("source_name"),
        mesh_object_state,
    )
    if int(dict(source_state.get("topology") or {}).get("solids", 0) or 0) != 1:
        raise NativeAnalyzeError("Fluid material requires one solid domain.")
    analysis_object, analysis_state_value = current_state(
        runtime,
        values.pop("analysis_name"),
        analysis_state,
    )
    for member in tuple(analysis_object.Group or ()):
        try:
            is_fluid = material_kind(member) == "fluid"
        except NativeAnalyzeError as exc:
            if exc.error_code == "NATIVE_ANALYZE_TARGET_TYPE_INVALID":
                continue
            raise
        if is_fluid and not bool(getattr(member, "Suppressed", False)):
            raise NativeAnalyzeError(
                f"Analysis already has fluid material {member.Name}."
            )
    analysis = {
        "object_name": analysis_state_value["object_name"],
        "expected_state_sha256": analysis_state_value["state_sha256"],
        "expected_member_count": int(analysis_state_value["member_count"]),
    }
    values["analysis"] = analysis
    values["references"] = [
        {
            "object_name": source_state["object_name"],
            "expected_state_sha256": source_state["state_sha256"],
            "subelements": ["Solid1"],
        }
    ]
    values["properties"] = {
        "name": values.pop("name"),
        "density_kg_m3": values.pop("density_kg_m3"),
        "kinematic_viscosity_m2_s": values.pop("kinematic_viscosity_m2_s"),
    }
    values.setdefault("label", "Fluid material")
    result = dict(
        runtime.execute(
            {"operation": "create_fluid_material", **values},
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_material")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["material_name"] = str(created["object_name"])
    return result


def _solver(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeSolverRuntime, "create")
    analysis = current_analysis_target(
        runtime,
        values.pop("analysis_name"),
    )
    values["analysis"] = analysis
    values.setdefault("label", "OpenFOAM")
    result = dict(
        runtime.execute(
            {"operation": "create_openfoam", **values},
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_solver")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["solver_name"] = str(created["object_name"])
    return result


def register_analyze_cfd_lifecycle_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_FLUID_MATERIAL, _material)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_OPENFOAM_SOLVER, _solver)
    )


def analyze_cfd_lifecycle_runtime_bindings(
    model_runtime: NativeAnalyzeModelRuntime,
    solver_runtime: NativeAnalyzeSolverRuntime,
) -> dict[str, Any]:
    if not isinstance(model_runtime, NativeAnalyzeModelRuntime):
        raise TypeError("model_runtime must be a NativeAnalyzeModelRuntime")
    if not isinstance(solver_runtime, NativeAnalyzeSolverRuntime):
        raise TypeError("solver_runtime must be a NativeAnalyzeSolverRuntime")
    return {
        ANALYZE_FLUID_MATERIAL: model_runtime,
        ANALYZE_OPENFOAM_SOLVER: solver_runtime,
    }
