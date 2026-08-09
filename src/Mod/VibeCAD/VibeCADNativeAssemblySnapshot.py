# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Assemble ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAssemblyComponents import (
    assembly_components,
    available_component_sources,
)
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyJointConnectors import (
    NativeAssemblyJointConnectorError,
    component_placement,
    component_shape_summary,
    connector_summary,
    placement_summary,
)
from VibeCADNativeAssemblyJointGraph import (
    active_regular_joints,
    reference_summary,
    solver_diagnostics,
)
from VibeCADNativeAssemblyState import read_active_assembly
from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_ASSEMBLIES = 16


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _solve_on_joint_creation() -> bool:
    try:
        import Preferences

        return bool(
            Preferences.preferences().GetBool("SolveInJointCreation", True)
        )
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return True


def _component_summary(component: Any, ground_joint: Any | None) -> dict[str, Any]:
    summary = concise_object(component)
    summary["grounded"] = ground_joint is not None
    summary["grounded_joint"] = (
        concise_object(ground_joint) if ground_joint is not None else None
    )
    try:
        summary["placement"] = placement_summary(component_placement(component))
    except NativeAssemblyJointConnectorError:
        summary["placement"] = None
    shape = component_shape_summary(component)
    if shape is not None:
        summary["shape"] = shape
    return summary


def _quantity_value(obj: Any, name: str) -> float | None:
    try:
        value = getattr(obj, name)
        return float(getattr(value, "Value", value))
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return None


def _joint_summary(joint: Any) -> dict[str, Any]:
    summary = concise_object(joint)
    joint_type = str(getattr(joint, "JointType", "") or "")
    summary["joint_type"] = joint_type
    summary["suppressed"] = bool(getattr(joint, "Suppressed", False))
    for key, index in (("first", 1), ("second", 2)):
        reference = getattr(joint, f"Reference{index}", None)
        try:
            summary[key] = connector_summary(
                reference,
                getattr(joint, f"Offset{index}"),
            )
        except (AttributeError, NativeAssemblyJointConnectorError):
            summary[key] = reference_summary(reference)
    if joint_type in {"Revolute", "Cylindrical"}:
        summary["angular_limits"] = {
            "minimum": {
                "enabled": bool(getattr(joint, "EnableAngleMin", False)),
                "degrees": _quantity_value(joint, "AngleMin"),
            },
            "maximum": {
                "enabled": bool(getattr(joint, "EnableAngleMax", False)),
                "degrees": _quantity_value(joint, "AngleMax"),
            },
        }
    return summary


def _assembly_summary(assembly: Any, active: Any | None) -> dict[str, Any]:
    result = concise_object(assembly)
    result["active"] = assembly is active
    children = list(getattr(assembly, "Group", []) or [])
    joint_groups = [
        child for child in children if getattr(child, "TypeId", "") == "Assembly::JointGroup"
    ]
    joints = tuple(
        joint
        for group in joint_groups
        for joint in active_regular_joints(group)
    )
    components = list(assembly_components(assembly))
    grounding_joints = tuple(
        joint
        for group in joint_groups
        for joint in active_grounded_joints(group)
    )
    grounded = {}
    for joint in grounding_joints:
        component = getattr(joint, "ObjectToGround", None)
        if component is not None:
            grounded.setdefault(component, joint)
    component_summaries = [
        _component_summary(component, grounded.get(component))
        for component in components[:32]
    ]
    result["counts"] = {
        "components": len(components),
        "joints": len(joints),
        "grounded": len(grounding_joints),
    }
    result["components"] = component_summaries
    result["joints"] = [_joint_summary(value) for value in joints[:32]]
    result["last_solver"] = solver_diagnostics(assembly)
    return result


def build_assembly_snapshot(document: Any) -> dict[str, Any]:
    assemblies = objects_of_type(
        document,
        "Assembly::AssemblyObject",
        "Assembly::Assembly",
    )
    active = read_active_assembly(document)
    sources, sources_truncated = available_component_sources(document, active)
    result = {
        "kind": "assembly",
        "assembly_count": len(assemblies),
        "solve_on_joint_creation": _solve_on_joint_creation(),
        "active_assembly": concise_object(active) if active is not None else None,
        "assemblies": [
            _assembly_summary(value, active) for value in assemblies[:MAX_ASSEMBLIES]
        ],
        "available_component_sources": sources,
    }
    if sources_truncated:
        result["available_component_sources_truncated"] = True
    return result
