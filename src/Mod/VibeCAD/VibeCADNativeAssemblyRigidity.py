# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact rigid/flexible conversion for active native Assembly links."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from VibeCADNativeAssemblyDiagnosisState import (
    AssemblyDiagnosisState,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyJointConnectors import placement_summary
from VibeCADNativeAssemblyJointGraph import (
    object_is_valid,
    require_joint_group,
    timeline_active,
)
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeState import NativeObjectIdentity
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


NATIVE_ASSEMBLY_RIGIDITY_FAILED = "NATIVE_ASSEMBLY_RIGIDITY_FAILED"
MAX_RIGIDITY_DOCUMENT_OBJECTS = 4_096
MAX_RIGIDITY_RELATION_ITEMS = 4_096


class NativeAssemblyRigidityError(NativeMutationError):
    """One exact AssemblyLink could not be converted safely."""

    def __init__(self, message: str) -> None:
        super().__init__(NATIVE_ASSEMBLY_RIGIDITY_FAILED, message)


@dataclass(frozen=True, slots=True)
class AssemblyRigiditySpec:
    assembly_ref: NativeObjectRef
    link_ref: NativeObjectRef
    expected_state_sha256: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    desired_rigid: bool


@dataclass(frozen=True, slots=True)
class _TimelineMember:
    object_id: int
    object_name: str
    root_id: int
    root_name: str
    role: str
    owner_id: int | None
    visible: bool
    suppressed: bool


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any
    members: tuple[_TimelineMember, ...]
    position: int

    @property
    def operation_ids(self) -> tuple[int, ...]:
        return tuple(member.object_id for member in self.members)

    @property
    def operation_names(self) -> tuple[str, ...]:
        return tuple(member.object_name for member in self.members)

    @property
    def visibility(self) -> tuple[bool, ...]:
        return tuple(member.visible for member in self.members)

    @property
    def suppression(self) -> tuple[bool, ...]:
        return tuple(member.suppressed for member in self.members)


@dataclass(frozen=True, slots=True)
class PreparedAssemblyRigidity:
    spec: AssemblyRigiditySpec
    assembly: Any
    parent_assembly: Any
    linked_assembly: Any
    link: Any
    diagnosis_before: AssemblyDiagnosisState
    expected_deleted_grounding: tuple[Any, ...]
    expected_deleted_grounding_records: tuple[dict[str, Any], ...]
    selection_before: dict[str, Any]
    objects_before: tuple[Any, ...]
    records_before: Mapping[str, Mapping[str, Any]]
    managed_before_names: frozenset[str]
    allowed_changed_before_names: frozenset[str]
    timeline_before: _TimelineState


def _exact_digest(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeAssemblyRigidityError(
            "expected_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _exact_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblyRigidityError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _identity_record(obj: Any) -> dict[str, Any]:
    result = object_reference(obj)
    object_id = getattr(obj, "ID", None)
    if type(object_id) is not int or object_id < 0:
        raise NativeAssemblyRigidityError(
            "The Assembly rigidity graph contains an invalid object identity."
        )
    return {**result, "object_id": object_id}


def _referenced_object_record(obj: Any | None) -> dict[str, Any] | None:
    return None if obj is None else _identity_record(obj)


def _placement_record(value: Any, field: str) -> dict[str, Any]:
    try:
        return placement_summary(value)
    except Exception as exc:
        raise NativeAssemblyRigidityError(
            f"The Assembly rigidity graph contains an invalid {field}."
        ) from exc


def _reference_record(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        component, raw_paths = value
        paths = tuple(str(path) for path in tuple(raw_paths or ()))
    except (ReferenceError, TypeError, ValueError) as exc:
        raise NativeAssemblyRigidityError(
            f"The Assembly rigidity graph contains an invalid {field}."
        ) from exc
    if component is None or len(paths) > 8 or any(len(path) > 512 for path in paths):
        raise NativeAssemblyRigidityError(
            f"The Assembly rigidity graph contains an invalid {field}."
        )
    return {"component": _identity_record(component), "paths": list(paths)}


def _relation_objects(value: Any, field: str) -> list[dict[str, Any]]:
    try:
        objects = tuple(value or ())
    except (ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyRigidityError(
            f"The Assembly rigidity graph contains an invalid {field}."
        ) from exc
    if len(objects) > MAX_RIGIDITY_RELATION_ITEMS:
        raise NativeAssemblyRigidityError(
            f"The Assembly rigidity graph exceeds the {MAX_RIGIDITY_RELATION_ITEMS}-item {field} bound."
        )
    return [_identity_record(obj) for obj in objects]


def _property_names(obj: Any) -> frozenset[str]:
    try:
        return frozenset(str(name) for name in tuple(obj.PropertiesList or ()))
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyRigidityError(
            "The Assembly rigidity graph has unreadable object properties."
        ) from exc


def _placement_locks(obj: Any, properties: frozenset[str]) -> dict[str, bool]:
    reader = getattr(obj, "getPropertyStatus", None)
    result: dict[str, bool] = {}
    for name in ("Placement", "LinkPlacement"):
        if name not in properties:
            continue
        try:
            statuses = tuple(reader(name) or ()) if callable(reader) else ()
        except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
            raise NativeAssemblyRigidityError(
                "The Assembly rigidity graph has unreadable placement locks."
            ) from exc
        result[name] = "ReadOnly" in statuses
    return result


def _object_record(obj: Any) -> dict[str, Any]:
    properties = _property_names(obj)
    record: dict[str, Any] = {
        **_identity_record(obj),
        "label": str(getattr(obj, "Label", "") or "")[:160],
    }
    if "Placement" in properties:
        record["placement"] = _placement_record(obj.Placement, "placement")
    locks = _placement_locks(obj, properties)
    if locks:
        record["placement_locks"] = locks
    if "Rigid" in properties:
        value = getattr(obj, "Rigid", None)
        if type(value) is not bool:
            raise NativeAssemblyRigidityError(
                "An AssemblyLink exposes an invalid Rigid property."
            )
        record["rigid"] = value
    if "LinkedObject" in properties:
        record["linked_object"] = _referenced_object_record(
            getattr(obj, "LinkedObject", None)
        )
    if "Group" in properties:
        record["group"] = _relation_objects(getattr(obj, "Group", ()), "Group")
    if "ElementList" in properties:
        record["elements"] = _relation_objects(
            getattr(obj, "ElementList", ()),
            "ElementList",
        )
    for name in ("Reference1", "Reference2"):
        if name in properties:
            record[name.lower()] = _reference_record(getattr(obj, name), name)
    for name in ("Offset1", "Offset2"):
        if name in properties:
            record[name.lower()] = _placement_record(getattr(obj, name), name)
    if "ObjectToGround" in properties:
        record["object_to_ground"] = _referenced_object_record(
            getattr(obj, "ObjectToGround", None)
        )
    for name in ("JointType", "Suppressed"):
        if name in properties:
            record[name.lower()] = getattr(obj, name)
    role = str(getattr(obj, "VibeCADTimelineRole", "") or "")
    if role:
        record["timeline_role"] = role
    owner = getattr(obj, "VibeCADTimelineOwner", None)
    if owner is not None:
        record["timeline_owner"] = _identity_record(owner)
    return record


def _document_records(document: Any) -> dict[str, dict[str, Any]]:
    objects = tuple(getattr(document, "Objects", ()) or ())
    if len(objects) > MAX_RIGIDITY_DOCUMENT_OBJECTS:
        raise NativeAssemblyRigidityError(
            f"The document exceeds the {MAX_RIGIDITY_DOCUMENT_OBJECTS}-object immediate rigidity-conversion bound."
        )
    records: dict[str, dict[str, Any]] = {}
    for obj in objects:
        record = _object_record(obj)
        name = str(record["object_name"])
        if name in records:
            raise NativeAssemblyRigidityError(
                "The Assembly rigidity graph contains duplicate object names."
            )
        records[name] = record
    return records


def _timeline_root(operation: Any, document: Any) -> Any:
    current = operation
    visited: set[int] = set()
    while str(getattr(current, "VibeCADTimelineRole", "") or "") == "resource":
        object_id = getattr(current, "ID", None)
        if type(object_id) is not int or object_id < 0 or object_id in visited:
            raise NativeAssemblyRigidityError(
                "The active document History has a cyclic or invalid resource owner chain."
            )
        visited.add(object_id)
        owner = getattr(current, "VibeCADTimelineOwner", None)
        if (
            owner is None
            or getattr(owner, "Document", None) is not document
            or document.getObject(str(getattr(owner, "Name", "") or "")) is not owner
        ):
            raise NativeAssemblyRigidityError(
                "The active document History has an orphaned or cross-document resource."
            )
        current = owner
    return current


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "") or "") != (
        "App::DocumentTimeline"
    ):
        raise NativeAssemblyRigidityError(
            "The active document has no exact native History."
        )
    try:
        operations = tuple(timeline.Operations or ())
        visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
        suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
        position = int(timeline.Position)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyRigidityError(
            "The active document History is unreadable."
        ) from exc
    if len(operations) != len(visibility) or len(operations) != len(suppression):
        raise NativeAssemblyRigidityError(
            "The active document History has inconsistent state arrays."
        )
    members: list[_TimelineMember] = []
    try:
        for operation, visible, suppressed in zip(
            operations,
            visibility,
            suppression,
            strict=True,
        ):
            object_id = int(operation.ID)
            object_name = str(operation.Name)
            role = str(getattr(operation, "VibeCADTimelineRole", "") or "")
            owner = getattr(operation, "VibeCADTimelineOwner", None)
            owner_id = None if owner is None else int(owner.ID)
            root = _timeline_root(operation, document)
            members.append(
                _TimelineMember(
                    object_id=object_id,
                    object_name=object_name,
                    root_id=int(root.ID),
                    root_name=str(root.Name),
                    role=role,
                    owner_id=owner_id,
                    visible=visible,
                    suppressed=suppressed,
                )
            )
    except NativeAssemblyRigidityError:
        raise
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyRigidityError(
            "The active document History contains an unreadable semantic block."
        ) from exc

    member_ids = tuple(member.object_id for member in members)
    member_names = tuple(member.object_name for member in members)
    if (
        any(value < 0 for value in member_ids)
        or len(set(member_ids)) != len(member_ids)
        or any(not value for value in member_names)
        or len(set(member_names)) != len(member_names)
        or any(member.role not in {"", "operation", "resource"} for member in members)
        or not 0 <= position <= len(members)
    ):
        raise NativeAssemblyRigidityError(
            "The active document History is malformed."
        )

    root_ids = frozenset(member.root_id for member in members)
    if not root_ids <= frozenset(member_ids):
        raise NativeAssemblyRigidityError(
            "The active document History is missing a semantic operation root."
        )
    seen_roots: set[int] = set()
    cursor = 0
    while cursor < len(members):
        root_id = members[cursor].root_id
        if root_id in seen_roots:
            raise NativeAssemblyRigidityError(
                "The active document History has a noncontiguous semantic block."
            )
        seen_roots.add(root_id)
        end = cursor + 1
        while end < len(members) and members[end].root_id == root_id:
            end += 1
        block = members[cursor:end]
        root = block[-1]
        if (
            root.object_id != root_id
            or root.root_name != root.object_name
            or root.role == "resource"
            or root.owner_id is not None
            or any(member.role != "resource" for member in block[:-1])
            or any(member.object_id == root_id for member in block[:-1])
        ):
            raise NativeAssemblyRigidityError(
                "The active document History has a noncanonical semantic block."
            )
        cursor = end

    return _TimelineState(timeline, tuple(members), position)


def _is_derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == type_id:
        return True
    reader = getattr(obj, "isDerivedFrom", None)
    if not callable(reader):
        return False
    try:
        return bool(reader(type_id))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _contains(container: Any, target: Any) -> bool:
    reader = getattr(container, "hasObject", None)
    if callable(reader):
        try:
            return bool(reader(target, True))
        except TypeError:
            try:
                return bool(reader(target))
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                return False
        except (AttributeError, ReferenceError, RuntimeError):
            return False
    pending = list(getattr(container, "Group", ()) or ())
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        if candidate is target:
            return True
        pending.extend(list(getattr(candidate, "Group", ()) or ()))
    return False


def _parent_assembly(link: Any) -> Any:
    reader = getattr(link, "getParentAssembly", None)
    parent = reader() if callable(reader) else None
    if parent is None:
        reader = getattr(link, "getParentGeoFeatureGroup", None)
        parent = reader() if callable(reader) else None
    if not _is_derived(parent, "Assembly::AssemblyObject"):
        raise NativeAssemblyRigidityError(
            "The exact AssemblyLink has no active parent Assembly."
        )
    return parent


def _linked_assembly(link: Any) -> Any:
    reader = getattr(link, "getLinkedAssembly", None)
    source = reader() if callable(reader) else getattr(link, "LinkedObject", None)
    if not _is_derived(source, "Assembly::AssemblyObject"):
        raise NativeAssemblyRigidityError(
            "The exact AssemblyLink has no linked Assembly definition."
        )
    return source


def _normalized_ground_target(joint: Any) -> Any | None:
    target = getattr(joint, "ObjectToGround", None)
    if str(getattr(target, "TypeId", "") or "") == "App::LinkElement":
        reader = getattr(target, "getLinkGroup", None)
        target = reader() if callable(reader) else None
    return target


def _expected_grounding_deletions(
    parent_assembly: Any,
    link: Any,
    desired_rigid: bool,
) -> tuple[Any, ...]:
    joint_group = require_joint_group(parent_assembly)
    result = []
    for joint in active_grounded_joints(joint_group):
        target = _normalized_ground_target(joint)
        if (desired_rigid and _contains(link, target)) or (
            not desired_rigid and target is link
        ):
            result.append(joint)
    return tuple(result)


def _graph_objects(root: Any) -> tuple[Any, ...]:
    result: list[Any] = []
    pending = [root]
    seen: set[int] = set()
    while pending:
        obj = pending.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        result.append(obj)
        if len(result) > MAX_RIGIDITY_RELATION_ITEMS:
            raise NativeAssemblyRigidityError(
                f"The AssemblyLink exceeds the {MAX_RIGIDITY_RELATION_ITEMS}-object managed-graph bound."
            )
        pending.extend(list(getattr(obj, "Group", ()) or ()))
        pending.extend(list(getattr(obj, "ElementList", ()) or ()))
    return tuple(result)


def _allowed_changed_objects(
    assembly: Any,
    parent: Any,
    link: Any,
    diagnosis: AssemblyDiagnosisState,
) -> tuple[Any, ...]:
    values = [assembly, parent, link, *_graph_objects(link)]
    try:
        parent_joint_group = require_joint_group(parent)
        values.extend((parent_joint_group, *tuple(parent_joint_group.Group or ())))
    except Exception as exc:
        raise NativeAssemblyRigidityError(
            "The parent Assembly joint graph is unavailable."
        ) from exc
    values.extend(record.obj for record in diagnosis.solver_state.records)
    result = []
    seen: set[int] = set()
    for obj in values:
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        result.append(obj)
    return tuple(result)


def _state_matches_spec(
    state: AssemblyDiagnosisState,
    spec: AssemblyRigiditySpec,
) -> bool:
    return bool(
        state.state_sha256 == spec.expected_state_sha256
        and len(state.components) == spec.expected_component_count
        and len(state.grounded_joints) == spec.expected_grounded_count
        and len(state.regular_joints) == spec.expected_joint_count
    )


def preflight_assembly_rigidity(
    document: Any,
    spec: AssemblyRigiditySpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedAssemblyRigidity:
    """Freeze one active AssemblyLink and every graph the core may rewrite."""

    if not isinstance(spec, AssemblyRigiditySpec):
        raise TypeError("spec must be an AssemblyRigiditySpec")
    if not isinstance(spec.assembly_ref, NativeObjectRef) or not isinstance(
        spec.link_ref,
        NativeObjectRef,
    ):
        raise TypeError("Assembly rigidity references must be NativeObjectRef values")
    if type(spec.desired_rigid) is not bool:
        raise TypeError("desired_rigid must be a bool")
    clean_spec = AssemblyRigiditySpec(
        assembly_ref=spec.assembly_ref,
        link_ref=spec.link_ref,
        expected_state_sha256=_exact_digest(spec.expected_state_sha256),
        expected_component_count=_exact_count(
            spec.expected_component_count,
            "expected_component_count",
            100_000,
        ),
        expected_grounded_count=_exact_count(
            spec.expected_grounded_count,
            "expected_grounded_count",
            256,
        ),
        expected_joint_count=_exact_count(
            spec.expected_joint_count,
            "expected_joint_count",
            256,
        ),
        desired_rigid=spec.desired_rigid,
    )
    try:
        assembly = resolve_object(
            document,
            clean_spec.assembly_ref,
            expected_types=("Assembly::AssemblyObject",),
        )
        link = resolve_object(
            document,
            clean_spec.link_ref,
            expected_types=("Assembly::AssemblyLink",),
        )
    except Exception as exc:
        raise NativeAssemblyRigidityError(str(exc)) from exc
    if not same_assembly(assembly, active_reader(document)) or not timeline_active(
        assembly
    ):
        raise NativeAssemblyRigidityError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    if not timeline_active(link) or not object_is_valid(link):
        raise NativeAssemblyRigidityError(
            "The exact AssemblyLink is not active and valid at the current History position."
        )
    parent = _parent_assembly(link)
    if (
        not timeline_active(parent)
        or not object_is_valid(parent)
        or not _contains(parent, link)
        or (parent is not assembly and not _contains(assembly, parent))
    ):
        raise NativeAssemblyRigidityError(
            "The exact AssemblyLink is not inside the human-active Assembly."
        )
    linked = _linked_assembly(link)
    if not timeline_active(linked) or not object_is_valid(linked):
        raise NativeAssemblyRigidityError(
            "The exact AssemblyLink source is not active and valid."
        )
    current_rigid = getattr(link, "Rigid", None)
    if type(current_rigid) is not bool:
        raise NativeAssemblyRigidityError(
            "The exact AssemblyLink exposes no valid Rigid state."
        )
    if current_rigid == clean_spec.desired_rigid:
        mode = "rigid" if clean_spec.desired_rigid else "flexible"
        raise NativeAssemblyRigidityError(
            f"The exact AssemblyLink is already {mode}; read current Assemble state and retry."
        )
    timeline = _timeline_state(document)
    if timeline.position != len(timeline.operation_ids):
        raise NativeAssemblyRigidityError(
            "Move History to its current tip before changing AssemblyLink rigidity."
        )
    try:
        diagnosis = capture_assembly_diagnosis_state(assembly)
    except Exception as exc:
        raise NativeAssemblyRigidityError(str(exc)) from exc
    if not _state_matches_spec(diagnosis, clean_spec):
        raise NativeAssemblyRigidityError(
            "The active Assembly changed; read current Assemble state and retry."
        )
    deleted_grounding = _expected_grounding_deletions(
        parent,
        link,
        clean_spec.desired_rigid,
    )
    objects = tuple(document.Objects)
    records = _document_records(document)
    managed = _graph_objects(link)
    allowed = _allowed_changed_objects(assembly, parent, link, diagnosis)
    return PreparedAssemblyRigidity(
        spec=clean_spec,
        assembly=assembly,
        parent_assembly=parent,
        linked_assembly=linked,
        link=link,
        diagnosis_before=diagnosis,
        expected_deleted_grounding=deleted_grounding,
        expected_deleted_grounding_records=tuple(
            _identity_record(joint) for joint in deleted_grounding
        ),
        selection_before=selection_reader(document),
        objects_before=objects,
        records_before=records,
        managed_before_names=frozenset(str(obj.Name) for obj in managed),
        allowed_changed_before_names=frozenset(str(obj.Name) for obj in allowed),
        timeline_before=timeline,
    )


def _changed_names(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> frozenset[str]:
    return frozenset(
        name
        for name in before.keys() & after.keys()
        if before[name] != after[name]
        and before[name].get("type_id") != "App::DocumentTimeline"
    )


def _history_is_exact(
    before: _TimelineState,
    after: _TimelineState,
    link_id: int,
    deleted_grounding_records: tuple[dict[str, Any], ...],
) -> bool:
    deleted_ids = frozenset(
        int(record["object_id"]) for record in deleted_grounding_records
    )
    before_roots = tuple(
        member for member in before.members if member.object_id == member.root_id
    )
    after_roots = tuple(
        member for member in after.members if member.object_id == member.root_id
    )
    before_root_ids = tuple(member.object_id for member in before_roots)
    after_root_ids = tuple(member.object_id for member in after_roots)
    expected_root_ids = tuple(
        root_id for root_id in before_root_ids if root_id not in deleted_ids
    )
    before_link_roots = tuple(
        member for member in before_roots if member.object_id == link_id
    )
    after_link_roots = tuple(
        member for member in after_roots if member.object_id == link_id
    )
    protected_before = tuple(
        member
        for member in before.members
        if member.root_id != link_id and member.root_id not in deleted_ids
    )
    protected_after = tuple(
        member for member in after.members if member.root_id != link_id
    )
    return bool(
        after.timeline is before.timeline
        and deleted_ids <= frozenset(before_root_ids)
        and deleted_ids.isdisjoint(after.operation_ids)
        and before_root_ids.count(link_id) == 1
        and after_root_ids == expected_root_ids
        and len(before_link_roots) == 1
        and before_link_roots == after_link_roots
        and protected_after == protected_before
        and after.position == len(after.operation_ids)
    )


def _history_mismatch_summary(
    before: _TimelineState,
    after: _TimelineState,
    link_id: int,
    deleted_grounding_records: tuple[dict[str, Any], ...],
) -> str:
    deleted_ids = frozenset(
        int(record["object_id"]) for record in deleted_grounding_records
    )
    before_roots = tuple(
        member for member in before.members if member.object_id == member.root_id
    )
    after_roots = tuple(
        member for member in after.members if member.object_id == member.root_id
    )
    expected_root_names = tuple(
        member.object_name
        for member in before_roots
        if member.object_id not in deleted_ids
    )
    actual_root_names = tuple(member.object_name for member in after_roots)
    missing_roots = [
        name for name in expected_root_names if name not in actual_root_names
    ]
    added_roots = [
        name for name in actual_root_names if name not in expected_root_names
    ]
    protected_before = tuple(
        member
        for member in before.members
        if member.root_id != link_id and member.root_id not in deleted_ids
    )
    protected_after = tuple(
        member for member in after.members if member.root_id != link_id
    )
    before_link_members = tuple(
        member.object_name for member in before.members if member.root_id == link_id
    )
    after_link_members = tuple(
        member.object_name for member in after.members if member.root_id == link_id
    )
    return (
        f"expected_roots={len(expected_root_names)}, actual_roots={len(actual_root_names)}, "
        f"missing_roots={missing_roots[:8]}, added_roots={added_roots[:8]}, "
        f"root_order_changed={not missing_roots and not added_roots and actual_root_names != expected_root_names}, "
        f"unrelated_block_changed={protected_after != protected_before}, "
        f"link_block={list(before_link_members[:8])}->{list(after_link_members[:8])}, "
        f"position={after.position}/{len(after.operation_ids)}"
    )


def apply_assembly_rigidity(
    document: Any,
    prepared: PreparedAssemblyRigidity,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> NativeMutationDraft:
    """Use the native Rigid property lifecycle and record its exact graph delta."""

    if not isinstance(prepared, PreparedAssemblyRigidity):
        raise TypeError("prepared must be PreparedAssemblyRigidity")
    current = preflight_assembly_rigidity(
        document,
        prepared.spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    if (
        current.assembly is not prepared.assembly
        or current.parent_assembly is not prepared.parent_assembly
        or current.linked_assembly is not prepared.linked_assembly
        or current.link is not prepared.link
        or current.objects_before != prepared.objects_before
        or current.records_before != prepared.records_before
        or current.timeline_before != prepared.timeline_before
    ):
        raise NativeAssemblyRigidityError(
            "The exact AssemblyLink graph changed after preflight."
        )
    try:
        prepared.link.Rigid = prepared.spec.desired_rigid
        recompute_result = document.recompute()
    except Exception as exc:
        raise NativeAssemblyRigidityError(
            "The native AssemblyLink rigidity lifecycle failed."
        ) from exc
    if recompute_result is False:
        raise NativeAssemblyRigidityError(
            "The AssemblyLink rigidity graph failed to recompute."
        )
    objects_after = tuple(document.Objects)
    records_after = _document_records(document)
    before_names = frozenset(prepared.records_before)
    after_names = frozenset(records_after)
    created_names = after_names - before_names
    deleted_names = before_names - after_names
    changed_names = _changed_names(prepared.records_before, records_after)
    managed_after_names = frozenset(
        str(obj.Name) for obj in _graph_objects(prepared.link)
    )
    try:
        diagnosis_after = capture_assembly_diagnosis_state(prepared.assembly)
    except Exception as exc:
        raise NativeAssemblyRigidityError(
            "The converted Assembly state could not be read."
        ) from exc
    allowed_after_names = frozenset(
        str(obj.Name)
        for obj in _allowed_changed_objects(
            prepared.assembly,
            prepared.parent_assembly,
            prepared.link,
            diagnosis_after,
        )
    )
    expected_ground_names = frozenset(
        str(record["object_name"])
        for record in prepared.expected_deleted_grounding_records
    )
    if not created_names <= managed_after_names:
        raise NativeAssemblyRigidityError(
            "Changing AssemblyLink rigidity created an unrelated document object."
        )
    if not deleted_names <= (
        prepared.managed_before_names | expected_ground_names
    ):
        raise NativeAssemblyRigidityError(
            "Changing AssemblyLink rigidity deleted an unrelated document object."
        )
    if not changed_names <= (
        prepared.allowed_changed_before_names | allowed_after_names
    ):
        raise NativeAssemblyRigidityError(
            "Changing AssemblyLink rigidity modified an unrelated document object."
        )
    timeline_after = _timeline_state(document)
    if not _history_is_exact(
        prepared.timeline_before,
        timeline_after,
        int(prepared.link.ID),
        prepared.expected_deleted_grounding_records,
    ):
        raise NativeAssemblyRigidityError(
            "Changing AssemblyLink rigidity changed unrelated History state: "
            + _history_mismatch_summary(
                prepared.timeline_before,
                timeline_after,
                int(prepared.link.ID),
                prepared.expected_deleted_grounding_records,
            )
            + "."
        )
    deleted_identities = tuple(
        NativeObjectIdentity(
            str(prepared.records_before[name]["document_uid"]),
            str(prepared.records_before[name]["object_name"]),
            str(prepared.records_before[name]["type_id"]),
        )
        for name in sorted(deleted_names)
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "objects_after": objects_after,
            "records_after": records_after,
            "diagnosis_after": diagnosis_after,
            "timeline_after": timeline_after,
            "created_names": created_names,
            "deleted_names": deleted_names,
            "changed_names": changed_names,
        },
        recompute_targets=(),
        created=tuple(
            object_identity(obj)
            for obj in objects_after
            if str(obj.Name) in created_names
        ),
        changed=tuple(
            object_identity(obj)
            for obj in objects_after
            if str(obj.Name) in changed_names
        ),
        deleted=deleted_identities,
    )


def verify_assembly_rigidity(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Prove mode, graph, History, activation, and selection before commit."""

    value = draft.value
    prepared: PreparedAssemblyRigidity = value["prepared"]
    link = prepared.link
    if (
        document.getObject(str(link.Name)) is not link
        or document.getObject(str(prepared.assembly.Name)) is not prepared.assembly
        or document.getObject(str(prepared.parent_assembly.Name))
        is not prepared.parent_assembly
        or not same_assembly(prepared.assembly, active_reader(document))
        or not timeline_active(link)
        or not object_is_valid(link)
        or _parent_assembly(link) is not prepared.parent_assembly
        or _linked_assembly(link) is not prepared.linked_assembly
        or bool(link.Rigid) != prepared.spec.desired_rigid
    ):
        raise NativeAssemblyRigidityError(
            "The AssemblyLink conversion failed its exact identity or mode postcondition."
        )
    if selection_reader(document) != prepared.selection_before:
        raise NativeAssemblyRigidityError(
            "Changing AssemblyLink rigidity changed the human selection."
        )
    if (
        tuple(document.Objects) != value["objects_after"]
        or _document_records(document) != value["records_after"]
        or _timeline_state(document) != value["timeline_after"]
    ):
        raise NativeAssemblyRigidityError(
            "The AssemblyLink graph changed after rigidity verification."
        )
    for record in prepared.expected_deleted_grounding_records:
        if document.getObject(str(record["object_name"])) is not None:
            raise NativeAssemblyRigidityError(
                "An incompatible grounded joint survived the AssemblyLink conversion."
            )
    diagnosis: AssemblyDiagnosisState = value["diagnosis_after"]
    current_diagnosis = capture_assembly_diagnosis_state(prepared.assembly)
    before = prepared.diagnosis_before
    if (
        current_diagnosis.state_sha256 != diagnosis.state_sha256
        or current_diagnosis.components != before.components
        or current_diagnosis.regular_joints != before.regular_joints
        or len(current_diagnosis.grounded_joints)
        != len(diagnosis.grounded_joints)
        or diagnosis.state_sha256 == before.state_sha256
    ):
        raise NativeAssemblyRigidityError(
            "The AssemblyLink conversion failed its exact Assembly-state postcondition."
        )
    return {
        "operation": "make_rigid" if prepared.spec.desired_rigid else "make_flexible",
        "assembly": _identity_record(prepared.assembly),
        "link": _identity_record(link),
        "linked_assembly": _identity_record(prepared.linked_assembly),
        "rigid": prepared.spec.desired_rigid,
        "component_count": len(diagnosis.components),
        "grounded_count": len(diagnosis.grounded_joints),
        "joint_count": len(diagnosis.regular_joints),
        "assembly_state_sha256": diagnosis.state_sha256,
        "removed_grounding": list(prepared.expected_deleted_grounding_records),
        "managed_resource_changes": {
            "created": len(value["created_names"]),
            "deleted": len(
                value["deleted_names"]
                - frozenset(
                    record["object_name"]
                    for record in prepared.expected_deleted_grounding_records
                )
            ),
        },
        "active_assembly_unchanged": True,
        "selection_unchanged": True,
    }
