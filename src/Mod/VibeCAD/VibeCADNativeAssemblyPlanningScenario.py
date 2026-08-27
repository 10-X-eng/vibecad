# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read one mutation-free planning scenario from the human-active Assembly."""

from __future__ import annotations

from typing import Any, Callable

from tool_impl.assembly_planning import AssemblyPlanningError, SCENARIO_SCHEMA, normalize_scenario
from VibeCADNativeAssemblyBeltJoint import belt_dependency_summary
from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyGearJoint import gears_dependency_summary
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyIdentity import (
    NativeAssemblyIdentityError,
    read_persistent_identity,
)
from VibeCADNativeAssemblyJointGraph import active_regular_joints, require_joint_group
from VibeCADNativeAssemblyRackPinionJoint import rack_pinion_dependency_summary
from VibeCADNativeAssemblyScrewJoint import screw_dependency_summary
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
        for key in (
            "compatibility", "fit", "joint_parameters", "coupling_parameters"
        ):
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


def _joint_record(
    joint: Any,
    paths_by_component: dict[int, dict[str, str]],
    interface_records: dict[str, dict[str, Any]],
    occurrence_ids: dict[int, str],
    grounded_components: set[int],
) -> dict[str, Any] | None:
    endpoints = [
        _endpoint_interface(getattr(joint, f"Reference{side}", None), paths_by_component)
        for side in (1, 2)
    ]
    if any(value is None for value in endpoints) or endpoints[0] == endpoints[1]:
        return None
    joint_type = str(getattr(joint, "JointType", "") or "").strip()
    if not joint_type:
        return None
    record = {
        "persistent_id": _identity(joint, "joint"),
        "joint_kind": joint_type.lower(),
        "interface_ids": endpoints,
    }
    references = [getattr(joint, f"Reference{side}", None) for side in (1, 2)]
    components = [
        value[0] if value is not None and len(value) == 2 else None
        for value in references
    ]
    moving = [
        index for index, component in enumerate(components)
        if component is not None and id(component) not in grounded_components
    ]
    if joint_type in {"Slider", "Revolute"} and len(moving) == 1:
        index = moving[0]
        occurrence_id = occurrence_ids.get(id(components[index]))
        interface = interface_records.get(str(endpoints[index]))
        if occurrence_id is not None:
            record["moving_occurrence_id"] = occurrence_id
        if isinstance(interface, dict) and isinstance(
            interface.get("coupling_parameters"), dict
        ):
            record["coupling_parameters"] = dict(
                interface["coupling_parameters"]
            )
    return record


def _dependency_names(summary: dict[str, Any], keys: tuple[str, str]) -> tuple[str, str] | None:
    values = []
    for key in keys:
        value = summary.get(key)
        if not isinstance(value, dict) or not str(value.get("object_name") or ""):
            return None
        values.append(str(value["object_name"]))
    return values[0], values[1]


def _mark_realized_couplings(
    active_joints: tuple[Any, ...],
    records_by_name: dict[str, dict[str, Any]],
) -> None:
    contracts = {
        "RackPinion": (
            "rack_pinion", rack_pinion_dependency_summary,
            ("rack_slider_joint", "pinion_revolute_joint"),
        ),
        "Screw": (
            "screw", screw_dependency_summary,
            ("slider_joint", "screw_revolute_joint"),
        ),
        "Gears": (
            "gears", gears_dependency_summary,
            ("first_revolute_joint", "second_revolute_joint"),
        ),
        "Belt": (
            "belt", belt_dependency_summary,
            ("first_revolute_joint", "second_revolute_joint"),
        ),
    }
    for coupling in active_joints:
        contract = contracts.get(str(getattr(coupling, "JointType", "") or ""))
        if contract is None:
            continue
        kind, reader, keys = contract
        summary = reader(coupling, active_joints)
        if not isinstance(summary, dict):
            continue
        names = _dependency_names(summary, keys)
        if names is None or any(name not in records_by_name for name in names):
            continue
        coupling_id = _identity(coupling, "joint")
        first_id = records_by_name[names[0]]["persistent_id"]
        second_id = records_by_name[names[1]]["persistent_id"]
        for name, other_id in ((names[0], second_id), (names[1], first_id)):
            records_by_name[name].setdefault("realized_couplings", []).append({
                "coupling_kind": kind,
                "other_joint_id": other_id,
                "coupling_joint_id": coupling_id,
            })


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
    interface_records: dict[str, dict[str, Any]] = {}
    paths_by_component: dict[int, dict[str, str]] = {}
    occurrence_ids: dict[int, str] = {}
    for component in assembly_components(assembly):
        occurrence_id = _identity(component, "occurrence")
        occurrences.append({
            "persistent_id": occurrence_id,
            "object_name": str(getattr(component, "Name", "") or ""),
        })
        occurrence_ids[id(component)] = occurrence_id
        records, paths = _interface_records(component, occurrence_id)
        interfaces.extend(records)
        interface_records.update(
            {record["persistent_id"]: record for record in records}
        )
        paths_by_component[id(component)] = paths

    joint_group = require_joint_group(assembly)
    grounded_components = {
        id(getattr(joint, "ObjectToGround", None))
        for joint in active_grounded_joints(joint_group)
        if getattr(joint, "ObjectToGround", None) is not None
    }
    active_joints = tuple(active_regular_joints(joint_group))
    joints: list[dict[str, Any]] = []
    records_by_name: dict[str, dict[str, Any]] = {}
    omitted: list[dict[str, str]] = []
    omitted_count = 0
    for joint in active_joints:
        record = _joint_record(
            joint,
            paths_by_component,
            interface_records,
            occurrence_ids,
            grounded_components,
        )
        if record is not None:
            joints.append(record)
            records_by_name[str(getattr(joint, "Name", "") or "")] = record
            continue
        omitted_count += 1
        if len(omitted) < MAX_OMITTED_JOINTS:
            omitted.append({
                "object_name": str(getattr(joint, "Name", "") or ""),
                "joint_type": str(getattr(joint, "JointType", "") or ""),
                "reason": "connector-not-bound-to-two-distinct-published-interfaces",
            })
    _mark_realized_couplings(active_joints, records_by_name)

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
        "coupling_declarations_extracted": any(
            "coupling_parameters" in joint for joint in joints
        ),
    }
    return normalized
