# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact desired-state grounding for components in the active Assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


MAX_GROUNDING_TARGETS = 16


class NativeAssemblyGroundingError(RuntimeError):
    """An exact Assembly grounding request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_GROUNDING_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class GroundingTargetSpec:
    component_ref: NativeObjectRef
    expected_grounded: bool


@dataclass(frozen=True, slots=True)
class GroundingSpec:
    assembly_ref: NativeObjectRef
    targets: tuple[GroundingTargetSpec, ...]
    grounded: bool
    expected_component_count: int
    expected_grounded_count: int


@dataclass(frozen=True, slots=True)
class PreparedGrounding:
    assembly: Any
    joint_group: Any
    targets: tuple[Any, ...]
    existing_joints: tuple[Any | None, ...]
    active_before: Any


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _owns_component(assembly: Any, component: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(
            UtilsAssembly.assemblyOwnsGroundingComponent(assembly, component)
        )
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _create_grounded_joint(component: Any, assembly: Any) -> Any:
    import CommandCreateJoint
    import JointObject

    joint = CommandCreateJoint.createGroundedJointFeature(component, assembly)
    JointObject.ensureViewProviderGroundedJoint(joint)
    return joint


def _grounded_view_is_exact(joint: Any) -> bool:
    try:
        import JointObject

        return isinstance(
            getattr(getattr(joint, "ViewObject", None), "Proxy", None),
            JointObject.ViewProviderGroundedJoint,
        )
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _joint_group(assembly: Any) -> Any:
    groups = [
        child
        for child in list(getattr(assembly, "Group", ()) or ())
        if str(getattr(child, "TypeId", "") or "") == "Assembly::JointGroup"
        and getattr(child, "Document", None) is getattr(assembly, "Document", None)
    ]
    if len(groups) != 1:
        raise NativeAssemblyGroundingError(
            "The human-active Assembly must contain one exact native joint group."
        )
    return groups[0]


def active_grounded_joints(
    joint_group: Any,
    *,
    timeline_active: Callable[[Any], bool] = _timeline_active,
) -> tuple[Any, ...]:
    """Return active grounded joints without creating or changing Assembly state."""

    return tuple(
        joint
        for joint in list(getattr(joint_group, "Group", ()) or ())
        if hasattr(joint, "ObjectToGround") and timeline_active(joint)
    )


def _placement_properties(component: Any) -> tuple[str, ...]:
    properties = set(getattr(component, "PropertiesList", ()) or ())
    return tuple(
        name for name in ("Placement", "LinkPlacement") if name in properties
    )


def _property_is_read_only(component: Any, name: str) -> bool:
    reader = getattr(component, "getPropertyStatus", None)
    if not callable(reader):
        return False
    try:
        return "ReadOnly" in tuple(reader(name) or ())
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _object_is_valid(obj: Any) -> bool:
    reader = getattr(obj, "isValid", None)
    if not callable(reader):
        return True
    try:
        return bool(reader())
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _grounding_map(joints: tuple[Any, ...]) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for joint in joints:
        component = getattr(joint, "ObjectToGround", None)
        if component in result:
            raise NativeAssemblyGroundingError(
                "The Assembly contains duplicate active grounding joints for one component."
            )
        result[component] = joint
    return result


def _validate_active_ground_graph(
    assembly: Any,
    joint_group: Any,
    joints: tuple[Any, ...],
    ownership_checker: Callable[[Any, Any], bool],
) -> dict[Any, Any]:
    document = assembly.Document
    for joint in joints:
        component = getattr(joint, "ObjectToGround", None)
        if (
            getattr(joint, "Document", None) is not document
            or document.getObject(str(getattr(joint, "Name", "") or "")) is not joint
            or component is None
            or getattr(component, "Document", None) is not document
            or not ownership_checker(assembly, component)
        ):
            raise NativeAssemblyGroundingError(
                "The Assembly contains a malformed active grounding joint."
            )
    return _grounding_map(joints)


def preflight_grounding(
    document: Any,
    spec: GroundingSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    timeline_active: Callable[[Any], bool] = _timeline_active,
    ownership_checker: Callable[[Any, Any], bool] = _owns_component,
) -> PreparedGrounding:
    """Resolve and validate a complete Ground/Unground request without mutation."""

    if not isinstance(spec, GroundingSpec):
        raise TypeError("spec must be a GroundingSpec")
    if not 1 <= len(spec.targets) <= MAX_GROUNDING_TARGETS:
        raise NativeAssemblyGroundingError(
            f"Grounding requires 1 to {MAX_GROUNDING_TARGETS} exact components."
        )
    assembly = resolve_object(
        document,
        spec.assembly_ref,
        expected_types=("Assembly::AssemblyObject",),
    )
    active = active_reader(document)
    if not same_assembly(assembly, active):
        raise NativeAssemblyGroundingError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    if not timeline_active(assembly):
        raise NativeAssemblyGroundingError(
            "The human-active Assembly is outside the current document history."
        )
    components = assembly_components(assembly)
    if len(components) != spec.expected_component_count:
        raise NativeAssemblyGroundingError(
            "The active Assembly component count changed; read current Assemble state and retry."
        )
    joint_group = _joint_group(assembly)
    if not timeline_active(joint_group) or not _object_is_valid(joint_group):
        raise NativeAssemblyGroundingError(
            "The human-active Assembly joint group is not active and valid."
        )
    joints = active_grounded_joints(joint_group, timeline_active=timeline_active)
    if len(joints) != spec.expected_grounded_count:
        raise NativeAssemblyGroundingError(
            "The active Assembly grounded count changed; read current Assemble state and retry."
        )
    by_component = _validate_active_ground_graph(
        assembly,
        joint_group,
        joints,
        ownership_checker,
    )
    resolved: list[Any] = []
    existing: list[Any | None] = []
    seen: set[str] = set()
    for target in spec.targets:
        component = resolve_object(document, target.component_ref)
        name = str(component.Name)
        if name in seen:
            raise NativeAssemblyGroundingError(
                "A grounding request cannot repeat the same exact component."
            )
        seen.add(name)
        if not timeline_active(component) or not ownership_checker(assembly, component):
            raise NativeAssemblyGroundingError(
                "An exact grounding target is not an active component of the human-active Assembly."
            )
        if not _placement_properties(component):
            raise NativeAssemblyGroundingError(
                "An exact grounding target has no lockable placement property."
            )
        joint = by_component.get(component)
        current = joint is not None
        if current is not target.expected_grounded:
            raise NativeAssemblyGroundingError(
                "An exact component grounding state changed; read current Assemble state and retry."
            )
        resolved.append(component)
        existing.append(joint)
    return PreparedGrounding(
        assembly=assembly,
        joint_group=joint_group,
        targets=tuple(resolved),
        existing_joints=tuple(existing),
        active_before=active,
    )


def _new_document_objects(document: Any, before: tuple[Any, ...]) -> tuple[Any, ...]:
    previous = {id(obj) for obj in before}
    return tuple(
        obj for obj in tuple(document.Objects) if id(obj) not in previous
    )


def _unique_identities(objects: tuple[Any, ...]) -> tuple[Any, ...]:
    result = []
    seen: set[tuple[str, str, str]] = set()
    for obj in objects:
        identity = object_identity(obj)
        key = (identity.document_uid, identity.object_name, identity.type_id)
        if key not in seen:
            seen.add(key)
            result.append(identity)
    return tuple(result)


def apply_grounding(
    document: Any,
    spec: GroundingSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    timeline_active: Callable[[Any], bool] = _timeline_active,
    ownership_checker: Callable[[Any, Any], bool] = _owns_component,
    joint_factory: Callable[[Any, Any], Any] = _create_grounded_joint,
) -> NativeMutationDraft:
    """Apply one bounded desired-state Ground/Unground mutation."""

    prepared = preflight_grounding(
        document,
        spec,
        active_reader=active_reader,
        timeline_active=timeline_active,
        ownership_checker=ownership_checker,
    )
    before_objects = tuple(document.Objects)
    before_names = tuple(str(obj.Name) for obj in before_objects)
    created_joints: list[Any] = []
    deleted_identities = []
    deleted_names: list[str] = []
    changed_targets = tuple(
        component
        for component, joint in zip(
            prepared.targets, prepared.existing_joints, strict=True
        )
        if (joint is not None) is not spec.grounded
    )
    if spec.grounded:
        for component, existing_joint in zip(
            prepared.targets, prepared.existing_joints, strict=True
        ):
            if existing_joint is not None:
                continue
            joint = joint_factory(component, prepared.assembly)
            if (
                joint is None
                or getattr(joint, "Document", None) is not document
                or getattr(joint, "ObjectToGround", None) is not component
                or joint not in list(getattr(prepared.joint_group, "Group", ()) or ())
            ):
                raise NativeAssemblyGroundingError(
                    "The native grounded-joint factory returned the wrong object graph."
                )
            created_joints.append(joint)
    else:
        for joint in prepared.existing_joints:
            if joint is None:
                continue
            deleted_identities.append(object_identity(joint))
            deleted_names.append(str(joint.Name))
            document.removeObject(str(joint.Name))

    changed_objects = (
        prepared.assembly,
        prepared.joint_group,
        *changed_targets,
    )
    return NativeMutationDraft(
        value={
            "spec": spec,
            "assembly": prepared.assembly,
            "joint_group": prepared.joint_group,
            "targets": prepared.targets,
            "changed_targets": changed_targets,
            "created_joints": tuple(created_joints),
            "deleted_names": tuple(deleted_names),
            "before_objects": before_objects,
            "before_names": before_names,
            "active_before": prepared.active_before,
        },
        recompute_targets=(
            *created_joints,
            *changed_targets,
            prepared.joint_group,
            prepared.assembly,
        ),
        created=tuple(object_identity(joint) for joint in created_joints),
        changed=_unique_identities(tuple(changed_objects)),
        deleted=tuple(deleted_identities),
    )


def verify_grounding(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    timeline_active: Callable[[Any], bool] = _timeline_active,
    ownership_checker: Callable[[Any, Any], bool] = _owns_component,
    view_checker: Callable[[Any], bool] = _grounded_view_is_exact,
) -> dict[str, Any]:
    """Prove exact grounding, lock state, history, and activation postconditions."""

    value = draft.value
    spec = value["spec"]
    assembly = value["assembly"]
    joint_group = value["joint_group"]
    targets = tuple(value["targets"])
    created = tuple(value["created_joints"])
    deleted_names = tuple(value["deleted_names"])
    before_names = set(value["before_names"])
    if (
        document.getObject(str(assembly.Name)) is not assembly
        or document.getObject(str(joint_group.Name)) is not joint_group
        or not _object_is_valid(assembly)
        or not _object_is_valid(joint_group)
        or not same_assembly(value["active_before"], active_reader(document))
        or len(assembly_components(assembly)) != spec.expected_component_count
    ):
        raise NativeAssemblyGroundingError(
            "Grounding changed the Assembly graph, validity, component count, or activation."
        )
    after_joints = active_grounded_joints(
        joint_group,
        timeline_active=timeline_active,
    )
    by_component = _validate_active_ground_graph(
        assembly,
        joint_group,
        after_joints,
        ownership_checker,
    )
    expected_count = (
        spec.expected_grounded_count + len(created) - len(deleted_names)
    )
    if len(after_joints) != expected_count:
        raise NativeAssemblyGroundingError(
            "Grounding produced the wrong number of active grounded joints."
        )
    new_objects = _new_document_objects(document, tuple(value["before_objects"]))
    after_names = {str(obj.Name) for obj in tuple(document.Objects)}
    if spec.grounded:
        created_names = {str(joint.Name) for joint in created}
        if after_names != before_names | created_names or new_objects != created or any(
            document.getObject(str(joint.Name)) is not joint
            or not timeline_active(joint)
            or str(getattr(joint, "VibeCADTimelineRole", "") or "") != "operation"
            or not view_checker(joint)
            for joint in created
        ):
            raise NativeAssemblyGroundingError(
                "Grounding created objects outside the exact native grounded-joint graph."
            )
    elif (
        after_names != before_names - set(deleted_names)
        or new_objects
        or any(document.getObject(name) is not None for name in deleted_names)
    ):
        raise NativeAssemblyGroundingError(
            "Ungrounding did not remove only the exact grounded joints."
        )

    results = []
    for component in targets:
        if (
            document.getObject(str(component.Name)) is not component
            or not ownership_checker(assembly, component)
        ):
            raise NativeAssemblyGroundingError(
                "An exact grounding target left the active Assembly."
            )
        joint = by_component.get(component)
        if (joint is not None) is not spec.grounded:
            raise NativeAssemblyGroundingError(
                "An exact component did not reach the requested grounding state."
            )
        native_reader = getattr(assembly, "isPartGrounded", None)
        if callable(native_reader) and bool(native_reader(component)) is not spec.grounded:
            raise NativeAssemblyGroundingError(
                "The native Assembly solver disagrees with the grounding result."
            )
        properties = _placement_properties(component)
        if any(
            _property_is_read_only(component, name) is not spec.grounded
            for name in properties
        ):
            raise NativeAssemblyGroundingError(
                "The component placement lock does not match its grounding state."
            )
        results.append(
            {
                "component": object_reference(component),
                "grounded": spec.grounded,
                "grounded_joint": object_reference(joint) if joint is not None else None,
            }
        )
    return {
        "assembly": object_reference(assembly),
        "grounded": spec.grounded,
        "targets": results,
        "component_count": len(assembly_components(assembly)),
        "grounded_count": len(after_joints),
        "active_assembly_unchanged": True,
        "changed": bool(value["changed_targets"]),
    }


def prepared_grounding_result(
    prepared: PreparedGrounding,
    spec: GroundingSpec,
) -> dict[str, Any]:
    """Return a verified desired-state result when no mutation is needed."""

    if any(
        (joint is not None) is not spec.grounded
        for joint in prepared.existing_joints
    ):
        raise NativeAssemblyGroundingError(
            "The grounding request still requires a document change."
        )
    return {
        "assembly": object_reference(prepared.assembly),
        "grounded": spec.grounded,
        "targets": [
            {
                "component": object_reference(component),
                "grounded": spec.grounded,
                "grounded_joint": (
                    object_reference(joint) if joint is not None else None
                ),
            }
            for component, joint in zip(
                prepared.targets, prepared.existing_joints, strict=True
            )
        ],
        "component_count": spec.expected_component_count,
        "grounded_count": spec.expected_grounded_count,
        "active_assembly_unchanged": True,
        "changed": False,
    }
