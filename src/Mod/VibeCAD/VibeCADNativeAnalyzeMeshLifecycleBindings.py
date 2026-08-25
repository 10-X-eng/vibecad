# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind focused Gmsh lifecycle tools to the shared mesh runtime."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeCurrentTargets import (
    current_analysis_target,
    current_state,
    current_target,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshLifecycleSchema import (
    ANALYZE_EDIT_GMSH_MESH,
    ANALYZE_FLOW_MESH,
    ANALYZE_GENERATE_GMSH,
    ANALYZE_GMSH_MESH,
    ANALYZE_SOLID_MESH,
)
from VibeCADNativeAnalyzeMeshRuntime import NativeAnalyzeMeshRuntime
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _edit_values(
    runtime: NativeAnalyzeMeshRuntime,
    values: dict[str, Any],
    *,
    ticket: Any,
) -> Mapping[str, Any]:
    _mesh, state = current_state(
        runtime,
        values.pop("mesh_name"),
        fem_mesh_definition_state,
    )
    if state.get("mesher") != "gmsh":
        raise NativeAnalyzeError("The named mesh is not a Gmsh definition.")
    target = {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }
    request = {"operation": "update_gmsh", "target": target}
    setting_names = ("maximum_size_mm", "minimum_size_mm", "element_order")
    if any(name in values for name in setting_names):
        settings = dict(state["settings"])
        for name in setting_names:
            if name in values:
                settings[name] = values.pop(name)
        settings["element_dimension"] = "from_shape"
        request["settings"] = settings
    if "label" in values:
        request["label"] = values.pop("label")
    if len(request) == 2:
        raise NativeAnalyzeError("Edit the mesh size or label.")
    result = dict(runtime.execute(request, ticket=ticket))
    result["mesh_name"] = str(target["object_name"])
    return result


def _create(call: Any, *, default_order: str) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeMeshRuntime):
        raise TypeError("A focused mesh call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused mesh call requires argument data.")
    values = dict(arguments)
    operation = values.pop("operation", None)
    if operation == "update":
        return _edit_values(
            runtime,
            values,
            ticket=getattr(call, "ticket", None),
        )
    if operation != "create":
        raise ValueError("A focused mesh call requires the create operation.")
    analysis = current_analysis_target(
        runtime,
        values.pop("analysis_name"),
    )
    source = current_target(
        runtime,
        values.pop("source_name"),
        mesh_object_state,
    )
    values["analysis"] = analysis
    values["source"] = source
    settings = {
        "maximum_size_mm": values.pop("maximum_size_mm"),
        "minimum_size_mm": values.pop("minimum_size_mm", 0.0),
        "element_dimension": "from_shape",
        "element_order": values.pop("element_order", default_order),
    }
    values.setdefault("label", "Gmsh mesh")
    result = dict(
        runtime.execute(
            {"operation": "create_gmsh", "settings": settings, **values},
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    result["source_name"] = str(source["object_name"])
    created = result.get("created_mesh_definition")
    if isinstance(created, Mapping) and created.get("object_name"):
        mesh_name = str(created["object_name"])
        result["mesh_name"] = mesh_name
        result["next"] = {
            "tool": ANALYZE_GENERATE_GMSH,
            "mesh_name": mesh_name,
        }
    return result


def _create_gmsh(call: Any) -> Mapping[str, Any]:
    return _create(call, default_order="first")


def _create_solid_mesh(call: Any) -> Mapping[str, Any]:
    return _create(call, default_order="second")


def _create_flow_mesh(call: Any) -> Mapping[str, Any]:
    return _create(call, default_order="first")


def _edit(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeMeshRuntime):
        raise TypeError("A focused mesh call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused mesh call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "edit":
        raise ValueError("A focused mesh call requires the edit operation.")
    return _edit_values(
        runtime,
        values,
        ticket=getattr(call, "ticket", None),
    )


def _generate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeMeshRuntime):
        raise TypeError("A focused mesh call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused mesh call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "generate":
        raise ValueError("A focused mesh call requires the generate operation.")
    target = current_target(
        runtime,
        values.pop("mesh_name"),
        fem_mesh_definition_state,
    )
    values["target"] = target
    values.setdefault("timeout_seconds", 300)
    result = dict(
        runtime.execute(
            {"operation": "generate_gmsh", **values},
            ticket=getattr(call, "ticket", None),
        )
    )
    result["mesh_name"] = str(target["object_name"])
    return result


def register_analyze_mesh_lifecycle_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_GMSH_MESH, _create_gmsh)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_SOLID_MESH, _create_solid_mesh)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_FLOW_MESH, _create_flow_mesh)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_EDIT_GMSH_MESH, _edit)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_GENERATE_GMSH, _generate)
    )


def analyze_mesh_lifecycle_runtime_bindings(
    runtime: NativeAnalyzeMeshRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeMeshRuntime):
        raise TypeError("runtime must be a NativeAnalyzeMeshRuntime")
    return {
        ANALYZE_GMSH_MESH: runtime,
        ANALYZE_SOLID_MESH: runtime,
        ANALYZE_FLOW_MESH: runtime,
        ANALYZE_EDIT_GMSH_MESH: runtime,
        ANALYZE_GENERATE_GMSH: runtime,
    }
