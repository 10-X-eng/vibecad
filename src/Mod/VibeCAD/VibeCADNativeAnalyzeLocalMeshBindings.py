# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind focused local mesh sizing to the shared refinement runtime."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeCurrentTargets import current_state, current_target
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLocalMeshSchema import (
    ANALYZE_EDIT_LOCAL_MESH_SIZE,
    ANALYZE_LOCAL_MESH_SIZE,
)
from VibeCADNativeAnalyzeMeshRefinementRuntime import (
    NativeAnalyzeMeshRefinementRuntime,
)
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _arguments(call: Any, operation: str) -> tuple[NativeAnalyzeMeshRefinementRuntime, dict]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeMeshRefinementRuntime):
        raise TypeError("A local mesh-size call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A local mesh-size call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != operation:
        raise ValueError(f"A local mesh-size call requires the {operation} operation.")
    return runtime, values


def _reference(
    runtime: NativeAnalyzeMeshRefinementRuntime,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    values = dict(value)
    source, state = current_state(
        runtime,
        values.pop("source_name"),
        mesh_object_state,
    )
    names = []
    for raw_name in values.pop("subelement_names"):
        name = str(raw_name)
        if "." in name:
            prefix, name = name.split(".", 1)
            if prefix != str(source.Name):
                raise NativeAnalyzeError(
                    f"{raw_name} does not belong to geometry source {source.Name}."
                )
        names.append(name)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "subelements": names,
    }


def _create(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, "create")
    mesh = current_target(
        runtime,
        values.pop("mesh_name"),
        fem_mesh_definition_state,
    )
    reference = _reference(runtime, values)
    result = dict(
        runtime.execute(
            {
                "operation": "create_region",
                "mesh": mesh,
                "label": "Local mesh size",
                "references": [reference],
                "definition": {
                    "element_size_mm": values.pop("element_size_mm"),
                },
            },
            ticket=getattr(call, "ticket", None),
        )
    )
    result["mesh_name"] = str(mesh["object_name"])
    created = result.get("created_mesh_refinement")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["refinement_name"] = str(created["object_name"])
    return result


def _edit(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, "edit")
    _refinement, state = current_state(
        runtime,
        values.pop("refinement_name"),
        mesh_refinement_state,
    )
    if state.get("refinement_mode") != "region":
        raise NativeAnalyzeError("The named refinement is not a local mesh size.")
    changes = dict(values.pop("changes"))
    request: dict[str, Any] = {
        "operation": "update_region",
        "target": {
            "object_name": state["object_name"],
            "expected_state_sha256": state["state_sha256"],
        },
    }
    if "applied_to" in changes:
        request["references"] = [
            _reference(runtime, dict(changes.pop("applied_to")))
        ]
    if "element_size_mm" in changes:
        request["definition"] = {
            "element_size_mm": changes.pop("element_size_mm")
        }
    if changes:
        raise NativeAnalyzeError("The local mesh-size edit contains unsupported changes.")
    result = dict(
        runtime.execute(
            request,
            ticket=getattr(call, "ticket", None),
        )
    )
    updated = result.get("updated_mesh_refinement")
    if isinstance(updated, Mapping) and updated.get("object_name"):
        result["refinement_name"] = str(updated["object_name"])
    return result


def register_analyze_local_mesh_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_LOCAL_MESH_SIZE, _create)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_EDIT_LOCAL_MESH_SIZE, _edit)
    )


def analyze_local_mesh_runtime_bindings(
    runtime: NativeAnalyzeMeshRefinementRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeMeshRefinementRuntime):
        raise TypeError("runtime must be a NativeAnalyzeMeshRefinementRuntime")
    return {
        ANALYZE_LOCAL_MESH_SIZE: runtime,
        ANALYZE_EDIT_LOCAL_MESH_SIZE: runtime,
    }
