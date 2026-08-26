# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind focused fluid creation tools to the shared FEM runtime."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeAnalyzeCurrentTargets import (
    current_analysis_target,
    current_state,
    current_target,
)
from VibeCADNativeAnalyzeFluidCreateSchema import (
    ANALYZE_BOUNDARY_VELOCITY,
    ANALYZE_EDIT_FLUID_BOUNDARY,
    ANALYZE_FLUID_BOUNDARY,
    ANALYZE_INITIAL_PRESSURE,
    ANALYZE_INITIAL_VELOCITY,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeFluidRuntime import NativeAnalyzeFluidRuntime
from VibeCADNativeAnalyzeFluidState import fluid_constraint_state
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


_KINDS = {
    ANALYZE_INITIAL_VELOCITY: "initial_flow_velocity",
    ANALYZE_INITIAL_PRESSURE: "initial_pressure",
    ANALYZE_BOUNDARY_VELOCITY: "flow_velocity",
    ANALYZE_FLUID_BOUNDARY: "fluid_boundary",
}
_LABELS = {
    ANALYZE_INITIAL_VELOCITY: "Initial flow velocity",
    ANALYZE_INITIAL_PRESSURE: "Initial pressure",
    ANALYZE_BOUNDARY_VELOCITY: "Boundary velocity",
    ANALYZE_FLUID_BOUNDARY: "Fluid boundary",
}


def _implementation(name: str) -> Callable[[Any], Mapping[str, Any]]:
    kind = _KINDS[name]

    def execute(call: Any) -> Mapping[str, Any]:
        runtime = getattr(call, "runtime", None)
        arguments = getattr(call, "arguments", None)
        if not isinstance(runtime, NativeAnalyzeFluidRuntime):
            raise TypeError("A focused fluid call requires its exact runtime.")
        if not isinstance(arguments, Mapping):
            raise TypeError("A focused fluid call requires argument data.")
        values = dict(arguments)
        if values.pop("operation", None) != "create":
            raise ValueError("A focused fluid call requires the create operation.")
        analysis = current_analysis_target(
            runtime,
            values.pop("analysis_name"),
        )
        values["analysis"] = analysis
        values.setdefault("label", _LABELS[name])
        if name == ANALYZE_FLUID_BOUNDARY:
            source = current_target(
                runtime,
                values.pop("source_name"),
                mesh_object_state,
            )
            references = [
                {
                    **source,
                    "subelements": list(values.pop("face_names")),
                }
            ]
            constraint = {
                "condition": values.pop("condition"),
                "turbulence": values.pop("turbulence", {"kind": "none"}),
                "thermal": values.pop("thermal", {"kind": "adiabatic"}),
            }
        else:
            source_name = values.pop("source_name", None)
            geometry_names = values.pop("geometry_names", None)
            references = []
            if source_name is not None:
                references = [
                    {
                        **current_target(runtime, source_name, mesh_object_state),
                        "subelements": list(geometry_names or ()),
                    }
                ]
            if name == ANALYZE_INITIAL_PRESSURE:
                constraint = {"pressure_pa": values.pop("pressure_pa")}
            else:
                constraint = {"components": values.pop("components")}
                if name == ANALYZE_BOUNDARY_VELOCITY:
                    constraint["normal_to_boundary"] = values.pop(
                        "normal_to_boundary", False
                    )
        result = dict(
            runtime.execute(
                {
                    "operation": f"create_{kind}",
                    "references": references,
                    "constraint": constraint,
                    **values,
                },
                ticket=getattr(call, "ticket", None),
            )
        )
        result["analysis_name"] = str(analysis["object_name"])
        created = result.get("created_constraint")
        if isinstance(created, Mapping) and created.get("object_name"):
            result["constraint_name"] = str(created["object_name"])
        return result

    return execute


def _edit_fluid_boundary(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeFluidRuntime):
        raise TypeError("A focused fluid call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused fluid call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "edit":
        raise ValueError("A focused fluid-boundary call requires the edit operation.")
    _boundary, state = current_state(
        runtime,
        values.pop("boundary_name"),
        fluid_constraint_state,
    )
    if state.get("constraint_kind") != "fluid_boundary":
        raise NativeAnalyzeError("The named constraint is not a fluid boundary.")
    changes = dict(values.pop("changes"))
    request: dict[str, Any] = {
        "operation": "update_fluid_boundary",
        "target": {
            "object_name": state["object_name"],
            "expected_state_sha256": state["state_sha256"],
        },
    }
    if "label" in changes:
        request["label"] = changes.pop("label")
    if "geometry" in changes:
        geometry = dict(changes.pop("geometry"))
        request["references"] = [
            {
                **current_target(
                    runtime,
                    geometry["source_name"],
                    mesh_object_state,
                ),
                "subelements": list(geometry["face_names"]),
            }
        ]
    if "condition" in changes:
        definition = dict(state["definition"])
        definition["condition"] = changes.pop("condition")
        request["constraint"] = definition
    if "turbulence" in changes:
        definition = dict(request.get("constraint") or state["definition"])
        definition["turbulence"] = changes.pop("turbulence")
        request["constraint"] = definition
    result = dict(
        runtime.execute(
            request,
            ticket=getattr(call, "ticket", None),
        )
    )
    updated = result.get("updated_constraint")
    if isinstance(updated, Mapping) and updated.get("object_name"):
        result["boundary_name"] = str(updated["object_name"])
    return result


def register_analyze_fluid_create_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in _KINDS:
        registry.register_implementation(
            NativeCapabilityImplementation(name, _implementation(name))
        )
    registry.register_implementation(
        NativeCapabilityImplementation(
            ANALYZE_EDIT_FLUID_BOUNDARY,
            _edit_fluid_boundary,
        )
    )


def analyze_fluid_create_runtime_bindings(
    runtime: NativeAnalyzeFluidRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeFluidRuntime):
        raise TypeError("runtime must be a NativeAnalyzeFluidRuntime")
    return {
        **{name: runtime for name in _KINDS},
        ANALYZE_EDIT_FLUID_BOUNDARY: runtime,
    }
