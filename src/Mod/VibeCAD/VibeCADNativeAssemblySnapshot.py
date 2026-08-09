# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Assemble ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_ASSEMBLIES = 16
_RESOURCE_GROUPS = frozenset(
    {
        "Assembly::JointGroup",
        "Assembly::BomGroup",
        "Assembly::ViewGroup",
        "Assembly::SimulationGroup",
    }
)


def _assembly_summary(assembly: Any) -> dict[str, Any]:
    result = concise_object(assembly)
    children = list(getattr(assembly, "Group", []) or [])
    joint_groups = [
        child for child in children if getattr(child, "TypeId", "") == "Assembly::JointGroup"
    ]
    joints = [
        joint
        for group in joint_groups
        for joint in list(getattr(group, "Group", []) or [])
    ]
    components = [
        child
        for child in children
        if str(getattr(child, "TypeId", "") or "") not in _RESOURCE_GROUPS
    ]
    result["counts"] = {
        "components": len(components),
        "joints": len(joints),
        "grounded": sum(
            1 for joint in joints if getattr(joint, "ObjectToGround", None) is not None
        ),
    }
    result["components"] = [concise_object(value) for value in components[:32]]
    result["joints"] = [concise_object(value) for value in joints[:32]]
    return result


def build_assembly_snapshot(document: Any) -> dict[str, Any]:
    assemblies = objects_of_type(
        document,
        "Assembly::AssemblyObject",
        "Assembly::Assembly",
    )
    return {
        "kind": "assembly",
        "assembly_count": len(assemblies),
        "assemblies": [
            _assembly_summary(value) for value in assemblies[:MAX_ASSEMBLIES]
        ],
    }
