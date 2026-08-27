# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read one mutation-free planning scenario from the human-active Assembly."""

from __future__ import annotations

from typing import Any, Callable

from tool_impl.assembly_planning import AssemblyPlanningError, SCENARIO_SCHEMA, normalize_scenario
from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyIdentity import (
    NativeAssemblyIdentityError,
    read_persistent_identity,
)
from VibeCADNativeAssemblyJointGraph import active_regular_joints, require_joint_group
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
import VibeCADReferenceContracts as reference_contracts


MAX_OMITTED_JOINTS = 256


def _identity(obj: Any, kind: str) -> str:
    try:
        value = read_persistent_identity(obj, expected_kind=kind)
    except NativeAssemblyIdentityError as exc:
        raise AssemblyPlanningError(str(exc)) from exc
    if value is None:
        raise AssemblyPlanningError(
            f"The live {kind} object lacks its persisted Assembly identity."
        )
    return value["persistent_id"]


def _interface_records(component: Any, occurrence_id: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    definitions = reference_contracts.native_interface_definitions(component)
    resources = list(getattr(component, "Group", ()) or ())
    records: list[dict[str, Any]] = []
    connector_paths: dict[str, str] = {}
    for name, definition in sorted(definitions.items()):
        selection = dict(definition.get("selection") or {})
        native_lcs = str(selection.get("native_lcs") or "")
        matches = [
            item for item in resources
            if str(getattr(item, "Name", "") or "") == native_lcs
        ]
        if len(matches) != 1:
            raise AssemblyPlanningError(
                f"Published interface {name!r} does not resolve exactly once on its occurrence."
            )
        interface_id = _identity(matches[0], "interface")
        if native_lcs in connector_paths:
            raise AssemblyPlanningError(
                f"Native connector path {native_lcs!r} maps to more than one interface."
            )
        connector = dict(definition.get("connector") or {})
        resolved = dict(definition.get("resolved") or {})
        record = {
            "persistent_id": interface_id,
            "occurrence_id": occurrence_id,
            "name": name,
            "kind": str(connector.get("kind") or ""),
            "allowed_joints": list(connector.get("allowed_joints") or ()),
        }
        for key in ("compatibility", "fit", "joint_parameters"):
            if key in connector:
                record[key] = connector[key]
        geometry_binding = resolved.get("geometry_binding")
        if isinstance(geometry_binding, dict):
            record["geometry_binding"] = dict(geometry_binding)
        records.append(record)
        connector_paths[native_lcs] = interface_id
    return records, connector_paths


def _endpoint_interface(reference: Any, paths_by_component: dict[int, dict[str, str]]) -> str | None:
    try:
        if reference is None or len(reference) != 2:
            return None
        component = reference[0]
        paths = list(reference[1] or ())
        component_paths = paths_by_component.get(id(component))
        if component_paths is None or len(paths) < 2:
            return None
        element_path = str(paths[0] or "")
        anchor_path = str(paths[1] or "")
        if not element_path or element_path != anchor_path:
            return None
        return component_paths.get(element_path)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return None


def _joint_record(joint: Any, paths_by_component: dict[int, dict[str, str]]) -> dict[str, Any] | None:
    endpoints = [
        _endpoint_interface(getattr(joint, f"Reference{side}", None), paths_by_component)
        for side in (1, 2)
    ]
    if any(value is None for value in endpoints) or endpoints[0] == endpoints[1]:
        return None
    joint_type = str(getattr(joint, "JointType", "") or "").strip()
    if not joint_type:
        return None
    return {
        "persistent_id": _identity(joint, "joint"),
        "joint_kind": joint_type.lower(),
        "interface_ids": endpoints,
    }


def read_live_planning_scenario(
    document: Any,
    *,
    guard: Callable[[], None] = lambda: None,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
) -> dict[str, Any]:
    """Extract a graph-bound scenario without assigning identities or mutating CAD."""

    guard()
    assembly = active_reader(document)
    if assembly is None:
        raise AssemblyPlanningError("No human-active Assembly is available for planning.")
    assembly_id = _identity(assembly, "assembly")
    occurrences: list[dict[str, Any]] = []
    interfaces: list[dict[str, Any]] = []
    paths_by_component: dict[int, dict[str, str]] = {}
    for component in assembly_components(assembly):
        occurrence_id = _identity(component, "occurrence")
        occurrences.append({
            "persistent_id": occurrence_id,
            "object_name": str(getattr(component, "Name", "") or ""),
        })
        records, paths = _interface_records(component, occurrence_id)
        interfaces.extend(records)
        paths_by_component[id(component)] = paths

    joint_group = require_joint_group(assembly)
    joints: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    omitted_count = 0
    for joint in active_regular_joints(joint_group):
        record = _joint_record(joint, paths_by_component)
        if record is not None:
            joints.append(record)
            continue
        omitted_count += 1
        if len(omitted) < MAX_OMITTED_JOINTS:
            omitted.append({
                "object_name": str(getattr(joint, "Name", "") or ""),
                "joint_type": str(getattr(joint, "JointType", "") or ""),
                "reason": "connector-not-bound-to-two-distinct-published-interfaces",
            })

    guard()
    if not same_assembly(assembly, active_reader(document)):
        raise AssemblyPlanningError(
            "The human-active Assembly changed while its planning scenario was read."
        )
    normalized = normalize_scenario({
        "schema": SCENARIO_SCHEMA,
        "scenario_id": f"assembly:{assembly_id}",
        "occurrences": occurrences,
        "interfaces": interfaces,
        "joints": joints,
    })
    normalized["extraction"] = {
        "schema": "vibecad-live-assembly-scenario-extraction-v1",
        "source": "human-active-native-assembly",
        "mutation_performed": False,
        "included_joint_count": len(joints),
        "omitted_joint_count": omitted_count,
        "omitted_joints": omitted,
        "omitted_joints_truncated": omitted_count > len(omitted),
        "coupling_declarations_extracted": False,
    }
    return normalized
