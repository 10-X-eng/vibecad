# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact non-mutating read of joints attached to one Assembly component."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from VibeCADNativeAssemblyJointConnectors import (
    MAX_JOINT_CONNECTOR_PATH,
    NativeAssemblyJointConnectorError,
    connector_summary,
)
from VibeCADNativeAssemblyJointGraph import MAX_ASSEMBLY_JOINTS, timeline_active
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import (
    NativeObjectRef,
    NativeTargetError,
    object_reference,
    read_current_selection,
    resolve_object,
)


MAX_ASSEMBLY_COMPONENTS = 100_000
DEFAULT_COMPONENT_JOINT_PAGE = 32
MAX_COMPONENT_JOINT_PAGE = 64


class NativeAssemblyComponentJointsError(RuntimeError):
    """The exact active-Assembly component/joint graph is unavailable."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_COMPONENT_JOINTS_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblyComponentJointState:
    assembly: Any
    components: tuple[Any, ...]
    component_records: tuple[dict[str, Any], ...]
    joints: tuple[Any, ...]
    joint_components: tuple[tuple[Any, Any], ...]
    joint_records: tuple[dict[str, Any], ...]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        return {
            "available": True,
            "state_sha256": self.state_sha256,
            "component_count": len(self.components),
            "joint_count": len(self.joints),
        }


@dataclass(frozen=True, slots=True)
class ComponentJointsSpec:
    assembly_ref: NativeObjectRef
    component_ref: NativeObjectRef
    expected_joint_graph_state_sha256: str
    expected_component_count: int
    expected_joint_count: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class PreparedComponentJoints:
    spec: ComponentJointsSpec
    state: AssemblyComponentJointState
    component: Any
    component_record: dict[str, Any]
    matching_indices: tuple[int, ...]
    active_before: Any
    selection_before: dict[str, Any]


def _identity_record(obj: Any) -> dict[str, Any]:
    result = object_reference(obj)
    object_id = getattr(obj, "ID", None)
    if type(object_id) is not int or object_id < 0:
        raise NativeAssemblyComponentJointsError(
            "The active Assembly component/joint graph has an invalid object identity."
        )
    return {**result, "object_id": object_id}


def _public_reference(value: dict[str, Any]) -> dict[str, str]:
    return {
        "document_uid": str(value["document_uid"]),
        "object_name": str(value["object_name"]),
        "type_id": str(value["type_id"]),
    }


def _live_object(obj: Any, document: Any) -> bool:
    name = str(getattr(obj, "Name", "") or "")
    reader = getattr(document, "getObject", None)
    return bool(
        name
        and getattr(obj, "Document", None) is document
        and callable(reader)
        and reader(name) is obj
    )


def _object_is_valid(obj: Any) -> bool:
    reader = getattr(obj, "isValid", None)
    if not callable(reader):
        return True
    try:
        return bool(reader())
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _active_components(assembly: Any) -> tuple[Any, ...]:
    try:
        import UtilsAssembly

        raw = UtilsAssembly.getMovablePartsWithin(assembly)
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyComponentJointsError(
            "The human-active Assembly movable-component graph is unavailable."
        ) from exc
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_ASSEMBLY_COMPONENTS:
        raise NativeAssemblyComponentJointsError(
            f"The active Assembly exceeds the {MAX_ASSEMBLY_COMPONENTS}-component Native bound."
        )
    document = getattr(assembly, "Document", None)
    components = tuple(raw)
    if len({id(component) for component in components}) != len(components):
        raise NativeAssemblyComponentJointsError(
            "The active Assembly movable-component graph contains duplicates."
        )
    for component in components:
        if (
            not _live_object(component, document)
            or not timeline_active(component)
            or not _object_is_valid(component)
        ):
            raise NativeAssemblyComponentJointsError(
                "The active Assembly contains a stale or inactive movable component."
            )
    return components


def _compiled_joints(assembly: Any) -> tuple[Any, ...]:
    try:
        raw = assembly.Joints
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyComponentJointsError(
            "The active Assembly does not expose its compiled joint graph."
        ) from exc
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_ASSEMBLY_JOINTS:
        raise NativeAssemblyComponentJointsError(
            f"The active Assembly exceeds the {MAX_ASSEMBLY_JOINTS}-joint Native bound."
        )
    joints = tuple(raw)
    if len({id(joint) for joint in joints}) != len(joints):
        raise NativeAssemblyComponentJointsError(
            "The active Assembly compiled joint graph contains duplicates."
        )
    return joints


def _joint_record(
    joint: Any,
    document: Any,
    components: frozenset[Any],
) -> tuple[tuple[Any, Any], dict[str, Any]]:
    properties = set(getattr(joint, "PropertiesList", ()) or ())
    if (
        not _live_object(joint, document)
        or not timeline_active(joint)
        or not _object_is_valid(joint)
        or not {"JointType", "Reference1", "Reference2", "Suppressed"}.issubset(
            properties
        )
        or bool(getattr(joint, "Suppressed", False))
    ):
        raise NativeAssemblyComponentJointsError(
            "The active Assembly compiled joint graph contains an invalid joint."
        )
    try:
        first_component = joint.Reference1[0]
        second_component = joint.Reference2[0]
        first = connector_summary(joint.Reference1, joint.Offset1)
        second = connector_summary(joint.Reference2, joint.Offset2)
    except (
        AttributeError,
        IndexError,
        NativeAssemblyJointConnectorError,
        ReferenceError,
        TypeError,
    ) as exc:
        raise NativeAssemblyComponentJointsError(
            "The active Assembly compiled joint graph has malformed connectors."
        ) from exc
    if (
        first_component is second_component
        or first_component not in components
        or second_component not in components
    ):
        raise NativeAssemblyComponentJointsError(
            "The active Assembly compiled joint graph references invalid components."
        )
    if any(
        len(str(connector[field])) > MAX_JOINT_CONNECTOR_PATH
        for connector in (first, second)
        for field in ("element_path", "anchor_path")
    ):
        raise NativeAssemblyComponentJointsError(
            "The active Assembly compiled joint graph has unbounded connector paths."
        )
    joint_type = str(getattr(joint, "JointType", "") or "")
    label = str(getattr(joint, "Label", "") or "")[:256]
    if not joint_type or len(joint_type) > 64:
        raise NativeAssemblyComponentJointsError(
            "The active Assembly compiled joint graph has invalid joint metadata."
        )
    record = {
        "joint": _identity_record(joint),
        "label": label,
        "joint_type": joint_type,
        "first_component_id": int(first_component.ID),
        "second_component_id": int(second_component.ID),
        "first": first,
        "second": second,
    }
    return (first_component, second_component), record


def capture_component_joint_state(assembly: Any) -> AssemblyComponentJointState:
    """Capture the exact ``Joints`` graph used by ``getJointsOfPart``."""

    document = getattr(assembly, "Document", None)
    if document is None or not _live_object(assembly, document):
        raise NativeAssemblyComponentJointsError(
            "The human-active Assembly is not one exact live document object."
        )
    components = _active_components(assembly)
    component_records = tuple(
        {
            "component": _identity_record(component),
            "label": str(getattr(component, "Label", "") or "")[:256],
        }
        for component in components
    )
    joints = _compiled_joints(assembly)
    component_set = frozenset(components)
    resolved = tuple(
        _joint_record(joint, document, component_set) for joint in joints
    )
    joint_components = tuple(item[0] for item in resolved)
    joint_records = tuple(item[1] for item in resolved)
    canonical = {
        "assembly": _identity_record(assembly),
        "components": component_records,
        "joints": joint_records,
    }
    try:
        encoded = json.dumps(
            canonical,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyComponentJointsError(
            "The active Assembly component/joint graph cannot be represented exactly."
        ) from exc
    return AssemblyComponentJointState(
        assembly=assembly,
        components=components,
        component_records=component_records,
        joints=joints,
        joint_components=joint_components,
        joint_records=joint_records,
        state_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def component_joint_state_summary(assembly: Any) -> dict[str, Any]:
    return capture_component_joint_state(assembly).summary()


def _exact_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblyComponentJointsError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _exact_active_assembly(
    context: NativeRuntimeContext,
    assembly_ref: NativeObjectRef,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    context.guard()
    try:
        assembly = resolve_object(
            context.document,
            assembly_ref,
            expected_types=("Assembly::AssemblyObject",),
        )
    except NativeTargetError as exc:
        raise NativeAssemblyComponentJointsError(str(exc)) from exc
    active = active_reader(context.document)
    if not same_assembly(assembly, active) or not timeline_active(assembly):
        raise NativeAssemblyComponentJointsError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    return assembly


def _resolve_component(
    context: NativeRuntimeContext,
    state: AssemblyComponentJointState,
    component_ref: NativeObjectRef,
) -> tuple[Any, dict[str, Any]]:
    try:
        component = resolve_object(context.document, component_ref)
    except NativeTargetError as exc:
        raise NativeAssemblyComponentJointsError(str(exc)) from exc
    matches = [
        index for index, candidate in enumerate(state.components) if candidate is component
    ]
    if len(matches) != 1:
        raise NativeAssemblyComponentJointsError(
            "The exact target is not one active movable component of the human-active Assembly."
        )
    try:
        import UtilsAssembly

        movable = bool(UtilsAssembly.isMovableAssemblyComponent(state.assembly, component))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        movable = False
    if not movable or not timeline_active(component):
        raise NativeAssemblyComponentJointsError(
            "The exact target is not one active movable component of the human-active Assembly."
        )
    index = matches[0]
    return component, state.component_records[index]


def _same_state(
    expected: AssemblyComponentJointState,
    current: AssemblyComponentJointState,
) -> bool:
    return (
        current.state_sha256 == expected.state_sha256
        and current.assembly is expected.assembly
        and len(current.components) == len(expected.components)
        and all(
            current_item is expected_item
            for current_item, expected_item in zip(
                current.components,
                expected.components,
            )
        )
        and len(current.joints) == len(expected.joints)
        and all(
            current_item is expected_item
            for current_item, expected_item in zip(current.joints, expected.joints)
        )
    )


def preflight_component_joints(
    context: NativeRuntimeContext,
    spec: ComponentJointsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedComponentJoints:
    """Freeze one exact component and its compiled joint page."""

    if not isinstance(spec, ComponentJointsSpec):
        raise TypeError("spec must be a ComponentJointsSpec")
    if not isinstance(spec.assembly_ref, NativeObjectRef):
        raise TypeError("spec.assembly_ref must be a NativeObjectRef")
    if not isinstance(spec.component_ref, NativeObjectRef):
        raise TypeError("spec.component_ref must be a NativeObjectRef")
    digest = spec.expected_joint_graph_state_sha256
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise NativeAssemblyComponentJointsError(
            "expected_joint_graph_state_sha256 must be one lowercase SHA-256 digest."
        )
    expected_component_count = _exact_count(
        spec.expected_component_count,
        "expected_component_count",
        MAX_ASSEMBLY_COMPONENTS,
    )
    expected_joint_count = _exact_count(
        spec.expected_joint_count,
        "expected_joint_count",
        MAX_ASSEMBLY_JOINTS,
    )
    offset = _exact_count(spec.offset, "offset", MAX_ASSEMBLY_JOINTS - 1)
    if type(spec.limit) is not int or not 1 <= spec.limit <= MAX_COMPONENT_JOINT_PAGE:
        raise NativeAssemblyComponentJointsError(
            "Component-joint limit must be an integer from 1 through "
            f"{MAX_COMPONENT_JOINT_PAGE}."
        )

    selection_before = selection_reader(context.document)
    assembly = _exact_active_assembly(context, spec.assembly_ref, active_reader)
    state = capture_component_joint_state(assembly)
    if (
        expected_component_count != len(state.components)
        or expected_joint_count != len(state.joints)
    ):
        raise NativeAssemblyComponentJointsError(
            "The active Assembly component/joint counts changed; read current Assemble state and retry."
        )
    if state.state_sha256 != digest:
        raise NativeAssemblyComponentJointsError(
            "The active Assembly component/joint graph changed; read current Assemble state and retry."
        )
    component, component_record = _resolve_component(
        context,
        state,
        spec.component_ref,
    )
    matching_indices = tuple(
        index
        for index, endpoints in enumerate(state.joint_components)
        if component in endpoints
    )
    if (not matching_indices and offset != 0) or (
        matching_indices and offset >= len(matching_indices)
    ):
        raise NativeAssemblyComponentJointsError(
            "Component-joint offset is outside the exact current joint set."
        )
    current_assembly = _exact_active_assembly(
        context,
        spec.assembly_ref,
        active_reader,
    )
    if (
        not same_assembly(assembly, current_assembly)
        or selection_reader(context.document) != selection_before
    ):
        raise NativeAssemblyComponentJointsError(
            "The active Assembly or human selection changed during component-joint preflight."
        )
    return PreparedComponentJoints(
        spec=spec,
        state=state,
        component=component,
        component_record=component_record,
        matching_indices=matching_indices,
        active_before=assembly,
        selection_before=selection_before,
    )


def _verify_unchanged(
    context: NativeRuntimeContext,
    prepared: PreparedComponentJoints,
    *,
    active_reader: Callable[[Any], Any | None],
    selection_reader: Callable[[Any], dict[str, Any]],
) -> None:
    current_assembly = _exact_active_assembly(
        context,
        prepared.spec.assembly_ref,
        active_reader,
    )
    current = capture_component_joint_state(current_assembly)
    component, _record = _resolve_component(
        context,
        current,
        prepared.spec.component_ref,
    )
    if (
        component is not prepared.component
        or not _same_state(prepared.state, current)
        or selection_reader(context.document) != prepared.selection_before
    ):
        raise NativeAssemblyComponentJointsError(
            "The active Assembly, target component, joint graph, or human selection changed during the read."
        )


def _result_record(
    record: dict[str, Any],
    endpoints: tuple[Any, Any],
    component: Any,
) -> dict[str, Any]:
    first_component, second_component = endpoints
    first_side = first_component is component
    result = {
        "joint": _public_reference(record["joint"]),
        "label": str(record["label"]),
        "joint_type": str(record["joint_type"]),
        "component_side": "first" if first_side else "second",
        "other_component": object_reference(
            second_component if first_side else first_component
        ),
        "first": dict(record["first"]),
        "second": dict(record["second"]),
    }
    if str(record["joint_type"]) in {"Revolute", "Slider"}:
        result["coupling_joint"] = str(record["joint"]["object_name"])
        result["coupling_component"] = str(component.Name)
    return result


def read_component_joints(
    context: NativeRuntimeContext,
    spec: ComponentJointsSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Return one exact bounded page matching ``getJointsOfPart`` semantics."""

    prepared = preflight_component_joints(
        context,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    end = min(len(prepared.matching_indices), spec.offset + spec.limit)
    page_indices = prepared.matching_indices[spec.offset : end]
    joints = [
        _result_record(
            prepared.state.joint_records[index],
            prepared.state.joint_components[index],
            prepared.component,
        )
        for index in page_indices
    ]
    _verify_unchanged(
        context,
        prepared,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    component = {
        **_public_reference(prepared.component_record["component"]),
        "label": str(prepared.component_record["label"]),
    }
    result = {
        "operation": "select_joints_of_component",
        "assembly": object_reference(prepared.state.assembly),
        "component": component,
        "joint_graph_state_sha256": prepared.state.state_sha256,
        "component_count": len(prepared.state.components),
        "joint_count": len(prepared.state.joints),
        "component_joint_count": len(prepared.matching_indices),
        "offset": spec.offset,
        "returned_count": len(joints),
        "joints": joints,
    }
    if end < len(prepared.matching_indices):
        result["next_offset"] = end
    return result
