# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Assemble ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAssemblyComponents import (
    assembly_components,
    available_component_sources,
)
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyState import read_active_assembly
from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_ASSEMBLIES = 16


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _assembly_summary(assembly: Any, active: Any | None) -> dict[str, Any]:
    result = concise_object(assembly)
    result["active"] = assembly is active
    children = list(getattr(assembly, "Group", []) or [])
    joint_groups = [
        child for child in children if getattr(child, "TypeId", "") == "Assembly::JointGroup"
    ]
    joints = [
        joint
        for group in joint_groups
        for joint in list(getattr(group, "Group", []) or [])
        if _timeline_active(joint)
    ]
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
    component_summaries = []
    for component in components[:32]:
        summary = concise_object(component)
        ground_joint = grounded.get(component)
        summary["grounded"] = ground_joint is not None
        summary["grounded_joint"] = (
            concise_object(ground_joint) if ground_joint is not None else None
        )
        component_summaries.append(summary)
    result["counts"] = {
        "components": len(components),
        "joints": len(joints),
        "grounded": len(grounding_joints),
    }
    result["components"] = component_summaries
    result["joints"] = [concise_object(value) for value in joints[:32]]
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
        "active_assembly": concise_object(active) if active is not None else None,
        "assemblies": [
            _assembly_summary(value, active) for value in assemblies[:MAX_ASSEMBLIES]
        ],
        "available_component_sources": sources,
    }
    if sources_truncated:
        result["available_component_sources_truncated"] = True
    return result
