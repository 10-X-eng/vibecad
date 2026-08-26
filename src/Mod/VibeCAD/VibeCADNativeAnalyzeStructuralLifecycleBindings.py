# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind focused structural assignments to the shared FEM runtimes."""

from __future__ import annotations

import copy
import math
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from VibeCADNativeAnalyzeCurrentTargets import (
    current_analysis_target,
    current_state,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLoadRuntime import NativeAnalyzeLoadRuntime
from VibeCADNativeAnalyzeLoadState import load_state
from VibeCADNativeAnalyzeMaterials import resolve_material_card_name
from VibeCADNativeAnalyzeModelRuntime import NativeAnalyzeModelRuntime
from VibeCADNativeAnalyzeStructuralLifecycleSchema import (
    ANALYZE_CATALOG_MATERIAL,
    ANALYZE_CENTRIFUGAL,
    ANALYZE_CUSTOM_MATERIAL,
    ANALYZE_EDIT_CENTRIFUGAL,
    ANALYZE_EDIT_DISPLACEMENT_SUPPORT,
    ANALYZE_EDIT_FIXED_SUPPORT,
    ANALYZE_EDIT_FORCE,
    ANALYZE_EDIT_GRAVITY,
    ANALYZE_EDIT_PRESSURE,
    ANALYZE_EDIT_RIGID_COUPLING,
    ANALYZE_EDIT_SPRING_SUPPORT,
    ANALYZE_DISPLACEMENT_SUPPORT,
    ANALYZE_FIXED_SUPPORT,
    ANALYZE_FORCE,
    ANALYZE_GRAVITY,
    ANALYZE_PRESSURE,
    ANALYZE_RIGID_COUPLING,
    ANALYZE_SOLID_REGION_MATERIAL,
    ANALYZE_SOLID_MATERIAL,
    ANALYZE_SPRING_SUPPORT,
)
from VibeCADNativeAnalyzeSupportRuntime import NativeAnalyzeSupportRuntime
from VibeCADNativeAnalyzeSupportState import support_condition_state
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


_DEFAULT_LABELS = {
    ANALYZE_SOLID_MATERIAL: "Solid material",
    ANALYZE_SOLID_REGION_MATERIAL: "Solid region material",
    ANALYZE_FIXED_SUPPORT: "Fixed support",
    ANALYZE_RIGID_COUPLING: "Rigid coupling",
    ANALYZE_DISPLACEMENT_SUPPORT: "Displacement support",
    ANALYZE_SPRING_SUPPORT: "Spring support",
    ANALYZE_FORCE: "Force",
    ANALYZE_PRESSURE: "Pressure",
    ANALYZE_GRAVITY: "Gravity",
    ANALYZE_CENTRIFUGAL: "Centrifugal load",
}


def _arguments(
    call: Any,
    runtime_type: type,
    *,
    operation: str = "create",
) -> tuple[Any, dict[str, Any]]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, runtime_type):
        raise TypeError("A focused structural call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused structural call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != operation:
        raise ValueError(f"A focused structural call requires the {operation} operation.")
    return runtime, values


def _geometry_reference(runtime: Any, values: dict[str, Any]) -> dict[str, Any]:
    source_name = values.pop("source_name")
    source, state = current_state(runtime, source_name, mesh_object_state)
    names = values.pop("subelement_names", None)
    if not names:
        count = int(dict(state.get("topology") or {}).get("solids", 0) or 0)
        if count < 1:
            raise NativeAnalyzeError(f"{source.Name} has no solid to receive material.")
        names = [f"Solid{index}" for index in range(1, count + 1)]
    else:
        normalized = []
        for raw in names:
            value = str(raw)
            if "." in value:
                prefix, value = value.split(".", 1)
                if prefix != str(source.Name):
                    raise NativeAnalyzeError(
                        f"{raw} does not belong to geometry source {source.Name}."
                    )
            normalized.append(value)
        names = normalized
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "subelements": list(names),
    }


def _assign_solid_material(
    call: Any,
    *,
    region_assignment: bool,
) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeModelRuntime)
    analysis = current_analysis_target(runtime, values.pop("analysis_name"))
    values["subelement_names"] = (
        values.pop("solid_regions") if region_assignment else None
    )
    material = dict(values.pop("material"))
    kind = str(material.pop("kind"))
    if kind == "catalog":
        values["material_uuid"] = material.pop("uuid")
    elif kind == "custom":
        values["properties"] = material.pop("properties")
    else:
        raise NativeAnalyzeError("The material source is unavailable.")
    reference = _geometry_reference(runtime, values)
    label_key = (
        ANALYZE_SOLID_REGION_MATERIAL
        if region_assignment
        else ANALYZE_SOLID_MATERIAL
    )
    values.setdefault("label", _DEFAULT_LABELS[label_key])
    result = dict(
        runtime.execute(
            {
                "operation": "create_solid_material",
                "analysis": analysis,
                "references": [reference],
                **values,
            },
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_material")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["material_name"] = str(created["object_name"])
    return result


def _solid_material(call: Any) -> Mapping[str, Any]:
    return _assign_solid_material(call, region_assignment=False)


def _solid_region_material(call: Any) -> Mapping[str, Any]:
    return _assign_solid_material(call, region_assignment=True)


def _focused_material_call(
    call: Any,
    values: dict[str, Any],
    material: dict[str, Any],
) -> Mapping[str, Any]:
    arguments = {
        "operation": "create",
        "analysis_name": values.pop("analysis_name"),
        "source_name": values.pop("source_name"),
        "material": material,
    }
    return _assign_solid_material(
        SimpleNamespace(
            runtime=getattr(call, "runtime", None),
            arguments=arguments,
            ticket=getattr(call, "ticket", None),
        ),
        region_assignment=False,
    )


def _catalog_material(call: Any) -> Mapping[str, Any]:
    _runtime, values = _arguments(call, NativeAnalyzeModelRuntime)
    material_uuid, _properties = resolve_material_card_name(
        values.pop("material_name"),
        category="solid",
    )
    return _focused_material_call(
        call,
        values,
        {"kind": "catalog", "uuid": material_uuid},
    )


def _custom_material(call: Any) -> Mapping[str, Any]:
    _runtime, values = _arguments(call, NativeAnalyzeModelRuntime)
    return _focused_material_call(
        call,
        values,
        {"kind": "custom", "properties": values.pop("properties")},
    )


def _fixed_support(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeSupportRuntime)
    analysis = current_analysis_target(runtime, values.pop("analysis_name"))
    reference = _geometry_reference(runtime, values)
    values.setdefault("label", _DEFAULT_LABELS[ANALYZE_FIXED_SUPPORT])
    result = dict(
        runtime.execute(
            {
                "operation": "create_fixed",
                "analysis": analysis,
                "references": [reference],
                **values,
            },
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_condition")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["support_name"] = str(created["object_name"])
    return result


_SUPPORT_CONDITION_FIELDS = {
    "rigid_body": ("reference_node_mm", "translation", "rotation"),
    "displacement": ("translation", "rotation", "flow_surface_force"),
    "spring": (
        "normal_stiffness_n_m",
        "tangential_stiffness_n_m",
        "elmer_component",
    ),
}


def _support_reference(
    runtime: NativeAnalyzeSupportRuntime,
    values: dict[str, Any],
    *,
    spring: bool = False,
) -> dict[str, Any]:
    if spring:
        values["subelement_names"] = values.pop("face_names")
    return _geometry_reference(runtime, values)


def _create_support(
    call: Any,
    *,
    kind: str,
    tool_name: str,
) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeSupportRuntime)
    analysis = current_analysis_target(runtime, values.pop("analysis_name"))
    reference = _support_reference(runtime, values, spring=kind == "spring")
    condition = {
        field: values.pop(field)
        for field in _SUPPORT_CONDITION_FIELDS[kind]
    }
    result = dict(
        runtime.execute(
            {
                "operation": f"create_{kind}",
                "analysis": analysis,
                "label": _DEFAULT_LABELS[tool_name],
                "references": [reference],
                "condition": condition,
            },
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_condition")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["support_name"] = str(created["object_name"])
    return result


def _rigid_coupling(call: Any) -> Mapping[str, Any]:
    return _create_support(
        call,
        kind="rigid_body",
        tool_name=ANALYZE_RIGID_COUPLING,
    )


def _displacement_support(call: Any) -> Mapping[str, Any]:
    return _create_support(
        call,
        kind="displacement",
        tool_name=ANALYZE_DISPLACEMENT_SUPPORT,
    )


def _spring_support(call: Any) -> Mapping[str, Any]:
    return _create_support(
        call,
        kind="spring",
        tool_name=ANALYZE_SPRING_SUPPORT,
    )


def _edit_support(
    call: Any,
    *,
    kind: str,
) -> Mapping[str, Any]:
    runtime, values = _arguments(
        call,
        NativeAnalyzeSupportRuntime,
        operation="edit",
    )
    _support, state = current_state(
        runtime,
        values.pop("support_name"),
        support_condition_state,
    )
    if state.get("condition_kind") != kind:
        raise NativeAnalyzeError(
            f"The named support is not {kind.replace('_', ' ')}."
        )
    changes = dict(values.pop("changes", {}))
    request: dict[str, Any] = {
        "operation": f"update_{kind}",
        "target": {
            "object_name": state["object_name"],
            "expected_state_sha256": state["state_sha256"],
        },
    }
    applied_to = changes.pop("applied_to", None)
    if isinstance(applied_to, Mapping):
        geometry = dict(applied_to)
        request["references"] = [
            _support_reference(runtime, geometry, spring=kind == "spring")
        ]
    condition_fields = _SUPPORT_CONDITION_FIELDS.get(kind, ())
    if any(field in changes for field in condition_fields):
        condition = copy.deepcopy(dict(state.get("definition") or {}))
        for field in condition_fields:
            if field in changes:
                condition[field] = changes.pop(field)
        request["condition"] = condition
    if changes:
        raise NativeAnalyzeError(
            "The support edit contains unsupported changes."
        )
    result = dict(
        runtime.execute(
            request,
            ticket=getattr(call, "ticket", None),
        )
    )
    updated = result.get("updated_condition")
    if isinstance(updated, Mapping) and updated.get("object_name"):
        result["support_name"] = str(updated["object_name"])
    return result


def _edit_fixed_support(call: Any) -> Mapping[str, Any]:
    return _edit_support(call, kind="fixed")


def _edit_rigid_coupling(call: Any) -> Mapping[str, Any]:
    return _edit_support(call, kind="rigid_body")


def _edit_displacement_support(call: Any) -> Mapping[str, Any]:
    return _edit_support(call, kind="displacement")


def _edit_spring_support(call: Any) -> Mapping[str, Any]:
    return _edit_support(call, kind="spring")


def _force_from_vector(value: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    try:
        components = tuple(float(value[axis]) for axis in ("x", "y", "z"))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise NativeAnalyzeError(
            "force_vector_n must contain finite x, y, and z values."
        ) from exc
    magnitude = math.sqrt(sum(component * component for component in components))
    if not math.isfinite(magnitude) or magnitude <= 0.0:
        raise NativeAnalyzeError("force_vector_n must have non-zero finite length.")
    return magnitude, {
        "kind": "vector",
        **{
            axis: component / magnitude
            for axis, component in zip(("x", "y", "z"), components, strict=True)
        },
    }


def _force(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeLoadRuntime)
    analysis = current_analysis_target(runtime, values.pop("analysis_name"))
    reference = _geometry_reference(runtime, values)
    values["force_n"], direction = _force_from_vector(
        dict(values.pop("force_vector_n"))
    )
    values.setdefault("label", _DEFAULT_LABELS[ANALYZE_FORCE])
    result = dict(
        runtime.execute(
            {
                "operation": "create_force",
                "analysis": analysis,
                "references": [reference],
                "direction": direction,
                **values,
            },
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_load")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["load_name"] = str(created["object_name"])
    return result


def _pressure(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeLoadRuntime)
    analysis = current_analysis_target(runtime, values.pop("analysis_name"))
    reference = _geometry_reference(runtime, values)
    values.setdefault("label", _DEFAULT_LABELS[ANALYZE_PRESSURE])
    result = dict(
        runtime.execute(
            {
                "operation": "create_pressure",
                "analysis": analysis,
                "references": [reference],
                **values,
            },
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_load")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["load_name"] = str(created["object_name"])
    return result


def _gravity(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeLoadRuntime)
    analysis = current_analysis_target(runtime, values.pop("analysis_name"))
    values.setdefault("label", _DEFAULT_LABELS[ANALYZE_GRAVITY])
    result = dict(
        runtime.execute(
            {
                "operation": "create_gravity",
                "analysis": analysis,
                **values,
            },
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_load")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["load_name"] = str(created["object_name"])
    return result


def _axis_reference(runtime: Any, value: Mapping[str, Any]) -> dict[str, Any]:
    source, state = current_state(runtime, value["source_name"], mesh_object_state)
    return {
        "object_name": str(source.Name),
        "expected_state_sha256": str(state["state_sha256"]),
        "subelement": str(value["edge_name"]),
    }


def _centrifugal_scope(runtime: Any, value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None or value.get("kind") == "all_bodies":
        return {"kind": "all_bodies"}
    reference = _geometry_reference(
        runtime,
        {
            "source_name": value["source_name"],
            "subelement_names": value["subelement_names"],
        },
    )
    return {"kind": "selected_geometry", "references": [reference]}


def _centrifugal(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeLoadRuntime)
    analysis = current_analysis_target(runtime, values.pop("analysis_name"))
    axis = _axis_reference(runtime, dict(values.pop("axis")))
    scope = _centrifugal_scope(runtime, values.pop("scope", None))
    values.setdefault("label", _DEFAULT_LABELS[ANALYZE_CENTRIFUGAL])
    result = dict(
        runtime.execute(
            {
                "operation": "create_centrifugal",
                "analysis": analysis,
                "axis": axis,
                "scope": scope,
                **values,
            },
            ticket=getattr(call, "ticket", None),
        )
    )
    result["analysis_name"] = str(analysis["object_name"])
    created = result.get("created_load")
    if isinstance(created, Mapping) and created.get("object_name"):
        result["load_name"] = str(created["object_name"])
    return result


def _edit_force(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(
        call,
        NativeAnalyzeLoadRuntime,
        operation="edit",
    )
    _load, state = current_state(runtime, values.pop("load_name"), load_state)
    if state.get("load_kind") != "force":
        raise NativeAnalyzeError("The named load is not a force.")
    changes = dict(values.pop("changes"))
    request: dict[str, Any] = {
        "operation": "update_force",
        "target": {
            "object_name": state["object_name"],
            "expected_state_sha256": state["state_sha256"],
        },
    }
    if "force_vector_n" in changes:
        request["force_n"], request["direction"] = _force_from_vector(
            dict(changes.pop("force_vector_n"))
        )
    if "applied_to" in changes:
        request["references"] = [
            _geometry_reference(runtime, dict(changes.pop("applied_to")))
        ]
    result = dict(
        runtime.execute(
            request,
            ticket=getattr(call, "ticket", None),
        )
    )
    updated = result.get("updated_load")
    if isinstance(updated, Mapping) and updated.get("object_name"):
        result["load_name"] = str(updated["object_name"])
    return result


def _edit_request(
    call: Any,
    kind: str,
) -> tuple[NativeAnalyzeLoadRuntime, dict[str, Any], dict[str, Any]]:
    runtime, values = _arguments(
        call,
        NativeAnalyzeLoadRuntime,
        operation="edit",
    )
    _load, state = current_state(runtime, values.pop("load_name"), load_state)
    if state.get("load_kind") != kind:
        raise NativeAnalyzeError(f"The named load is not {kind}.")
    return runtime, dict(values.pop("changes")), {
        "operation": f"update_{kind}",
        "target": {
            "object_name": state["object_name"],
            "expected_state_sha256": state["state_sha256"],
        },
    }


def _updated_load_result(call: Any, runtime: Any, request: dict[str, Any]) -> dict[str, Any]:
    result = dict(
        runtime.execute(
            request,
            ticket=getattr(call, "ticket", None),
        )
    )
    updated = result.get("updated_load")
    if isinstance(updated, Mapping) and updated.get("object_name"):
        result["load_name"] = str(updated["object_name"])
    return result


def _edit_pressure(call: Any) -> Mapping[str, Any]:
    runtime, changes, request = _edit_request(call, "pressure")
    if "applied_to" in changes:
        request["references"] = [
            _geometry_reference(runtime, dict(changes.pop("applied_to")))
        ]
    request.update(changes)
    return _updated_load_result(call, runtime, request)


def _edit_gravity(call: Any) -> Mapping[str, Any]:
    runtime, changes, request = _edit_request(call, "gravity")
    request.update(changes)
    return _updated_load_result(call, runtime, request)


def _edit_centrifugal(call: Any) -> Mapping[str, Any]:
    runtime, changes, request = _edit_request(call, "centrifugal")
    if "axis" in changes:
        request["axis"] = _axis_reference(runtime, dict(changes.pop("axis")))
    if "scope" in changes:
        request["scope"] = _centrifugal_scope(runtime, dict(changes.pop("scope")))
    request.update(changes)
    return _updated_load_result(call, runtime, request)


_IMPLEMENTATIONS: dict[str, Callable[[Any], Mapping[str, Any]]] = {
    ANALYZE_CATALOG_MATERIAL: _catalog_material,
    ANALYZE_CUSTOM_MATERIAL: _custom_material,
    ANALYZE_SOLID_MATERIAL: _solid_material,
    ANALYZE_SOLID_REGION_MATERIAL: _solid_region_material,
    ANALYZE_FIXED_SUPPORT: _fixed_support,
    ANALYZE_EDIT_FIXED_SUPPORT: _edit_fixed_support,
    ANALYZE_RIGID_COUPLING: _rigid_coupling,
    ANALYZE_EDIT_RIGID_COUPLING: _edit_rigid_coupling,
    ANALYZE_DISPLACEMENT_SUPPORT: _displacement_support,
    ANALYZE_EDIT_DISPLACEMENT_SUPPORT: _edit_displacement_support,
    ANALYZE_SPRING_SUPPORT: _spring_support,
    ANALYZE_EDIT_SPRING_SUPPORT: _edit_spring_support,
    ANALYZE_FORCE: _force,
    ANALYZE_EDIT_FORCE: _edit_force,
    ANALYZE_PRESSURE: _pressure,
    ANALYZE_EDIT_PRESSURE: _edit_pressure,
    ANALYZE_GRAVITY: _gravity,
    ANALYZE_EDIT_GRAVITY: _edit_gravity,
    ANALYZE_CENTRIFUGAL: _centrifugal,
    ANALYZE_EDIT_CENTRIFUGAL: _edit_centrifugal,
}


def register_analyze_structural_lifecycle_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name, execute in _IMPLEMENTATIONS.items():
        registry.register_implementation(NativeCapabilityImplementation(name, execute))


def analyze_structural_lifecycle_runtime_bindings(
    model: NativeAnalyzeModelRuntime,
    support: NativeAnalyzeSupportRuntime,
    load: NativeAnalyzeLoadRuntime,
) -> dict[str, Any]:
    return {
        ANALYZE_CATALOG_MATERIAL: model,
        ANALYZE_CUSTOM_MATERIAL: model,
        ANALYZE_SOLID_MATERIAL: model,
        ANALYZE_SOLID_REGION_MATERIAL: model,
        ANALYZE_FIXED_SUPPORT: support,
        ANALYZE_EDIT_FIXED_SUPPORT: support,
        ANALYZE_RIGID_COUPLING: support,
        ANALYZE_EDIT_RIGID_COUPLING: support,
        ANALYZE_DISPLACEMENT_SUPPORT: support,
        ANALYZE_EDIT_DISPLACEMENT_SUPPORT: support,
        ANALYZE_SPRING_SUPPORT: support,
        ANALYZE_EDIT_SPRING_SUPPORT: support,
        ANALYZE_FORCE: load,
        ANALYZE_EDIT_FORCE: load,
        ANALYZE_PRESSURE: load,
        ANALYZE_EDIT_PRESSURE: load,
        ANALYZE_GRAVITY: load,
        ANALYZE_EDIT_GRAVITY: load,
        ANALYZE_CENTRIFUGAL: load,
        ANALYZE_EDIT_CENTRIFUGAL: load,
    }
