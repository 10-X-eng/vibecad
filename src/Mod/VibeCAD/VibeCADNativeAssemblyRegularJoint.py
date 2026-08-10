# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact regular-joint creation in the human-active Assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyJointConnectors import (
    JointConnectorSpec,
    NativeAssemblyJointConnectorError,
    ResolvedJointConnector,
    component_placement,
    connector_summary,
    placement_is_same,
    placement_summary,
    resolve_joint_connector,
)
from VibeCADNativeAssemblyJointGraph import (
    NativeAssemblyJointGraphError,
    active_regular_joints,
    object_is_valid,
    require_joint_group,
    solver_diagnostics,
    timeline_active,
)
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


REGULAR_JOINT_TYPE_INDICES = {
    "Fixed": 0,
    "Revolute": 1,
    "Cylindrical": 2,
    "Slider": 3,
    "Ball": 4,
    "Distance": 5,
    "Parallel": 6,
    "Perpendicular": 7,
    "Angle": 8,
    "RackPinion": 9,
    "Screw": 10,
    "Gears": 11,
    "Belt": 12,
}
REGULAR_JOINT_PROPERTIES = {
    "Fixed": frozenset(),
    "Revolute": frozenset(
        {
            "EnableAngleMin",
            "AngleMin",
            "EnableAngleMax",
            "AngleMax",
        }
    ),
    "Cylindrical": frozenset(
        {
            "EnableLengthMin",
            "LengthMin",
            "EnableLengthMax",
            "LengthMax",
            "EnableAngleMin",
            "AngleMin",
            "EnableAngleMax",
            "AngleMax",
        }
    ),
    "Slider": frozenset(
        {
            "EnableLengthMin",
            "LengthMin",
            "EnableLengthMax",
            "LengthMax",
        }
    ),
    "Ball": frozenset(),
    "Distance": frozenset({"Distance"}),
    "Parallel": frozenset(),
    "Perpendicular": frozenset(),
    "Angle": frozenset({"Angle"}),
    "RackPinion": frozenset({"Distance"}),
    "Screw": frozenset({"Distance"}),
    "Gears": frozenset({"Distance", "Distance2"}),
    "Belt": frozenset({"Distance", "Distance2"}),
}


class NativeAssemblyRegularJointError(RuntimeError):
    """An exact regular-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_REGULAR_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class RegularJointPropertySpec:
    name: str
    value: bool | float


@dataclass(frozen=True, slots=True)
class RegularJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    joint_type: str
    type_index: int
    label: str
    reverse: bool
    properties: tuple[RegularJointPropertySpec, ...]
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


@dataclass(frozen=True, slots=True)
class PreparedRegularJoint:
    assembly: Any
    joint_group: Any
    first: ResolvedJointConnector
    second: ResolvedJointConnector
    active_before: Any
    selection_before: dict[str, Any]
    regular_joints_before: tuple[Any, ...]
    grounded_joints_before: tuple[Any, ...]


def _solve_on_creation() -> bool:
    try:
        import Preferences

        return bool(
            Preferences.preferences().GetBool("SolveInJointCreation", True)
        )
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return True


def _owns_grounded_component(assembly: Any, component: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(
            UtilsAssembly.assemblyOwnsGroundingComponent(assembly, component)
        )
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _create_regular_joint(
    assembly: Any,
    joint_group: Any,
    spec: RegularJointSpec,
) -> Any:
    import JointObject

    joint = joint_group.newObject("App::FeaturePython", "Joint")
    if joint is None:
        raise NativeAssemblyRegularJointError(
            "The native Assembly joint factory returned no object."
        )
    joint.Label = spec.label
    JointObject.Joint(joint, spec.type_index)
    JointObject.ensureViewProviderJoint(joint)
    return joint


def _regular_view_is_exact(joint: Any) -> bool:
    try:
        import JointObject

        return isinstance(
            getattr(getattr(joint, "ViewObject", None), "Proxy", None),
            JointObject.ViewProviderJoint,
        )
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _regular_proxy_is_exact(joint: Any) -> bool:
    try:
        import JointObject

        return isinstance(getattr(joint, "Proxy", None), JointObject.Joint)
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _belongs_to_assembly(obj: Any, assembly: Any) -> bool:
    try:
        import UtilsAssembly

        return (
            UtilsAssembly.findOwningAssembly(obj, include_inactive=True)
            is assembly
        )
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _reference_is_valid(reference: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isRefValid(reference, 2))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _is_movable_component(assembly: Any, component: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isMovableAssemblyComponent(assembly, component))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _validate_grounded_graph(assembly: Any, joints: tuple[Any, ...]) -> None:
    document = assembly.Document
    seen: set[Any] = set()
    for joint in joints:
        component = getattr(joint, "ObjectToGround", None)
        if (
            getattr(joint, "Document", None) is not document
            or document.getObject(str(getattr(joint, "Name", "") or "")) is not joint
            or not object_is_valid(joint)
            or not _belongs_to_assembly(joint, assembly)
            or component is None
            or component in seen
            or not _owns_grounded_component(assembly, component)
        ):
            raise NativeAssemblyRegularJointError(
                "The active Assembly contains a malformed or duplicate grounding joint."
            )
        seen.add(component)


def _regular_joint_components(joint: Any) -> tuple[Any, Any] | None:
    try:
        if len(joint.Reference1[1]) < 2 or len(joint.Reference2[1]) < 2:
            return None
        first = joint.Reference1[0]
        second = joint.Reference2[0]
    except (AttributeError, IndexError, ReferenceError, TypeError):
        return None
    if first is None or second is None:
        return None
    return first, second


def _validate_regular_graph(
    assembly: Any,
    joint_group: Any,
    joints: tuple[Any, ...],
) -> None:
    document = assembly.Document
    active_children = [
        child
        for child in list(getattr(joint_group, "Group", ()) or ())
        if timeline_active(child)
    ]
    if len(active_children) > 256:
        raise NativeAssemblyRegularJointError(
            "The active Assembly exceeds the 256-joint Native bound."
        )
    for joint in joints:
        components = _regular_joint_components(joint)
        if (
            getattr(joint, "Document", None) is not document
            or document.getObject(str(getattr(joint, "Name", "") or "")) is not joint
            or not object_is_valid(joint)
            or components is None
            or components[0] is components[1]
            or not _belongs_to_assembly(joint, assembly)
            or not _reference_is_valid(joint.Reference1)
            or not _reference_is_valid(joint.Reference2)
            or not _is_movable_component(assembly, components[0])
            or not _is_movable_component(assembly, components[1])
        ):
            raise NativeAssemblyRegularJointError(
                "The active Assembly contains a malformed regular joint."
            )
    classified = set(joints)
    classified.update(
        child for child in active_children if hasattr(child, "ObjectToGround")
    )
    if set(active_children) != classified:
        raise NativeAssemblyRegularJointError(
            "The active Assembly joint group contains an unknown active object."
        )


def _reject_duplicate_joint_pair(
    joints: tuple[Any, ...],
    first: Any,
    second: Any,
    joint_type: str,
) -> None:
    requested = {first, second}
    for joint in joints:
        if str(getattr(joint, "JointType", "") or "") != joint_type:
            continue
        components = _regular_joint_components(joint)
        if components is not None and set(components) == requested:
            raise NativeAssemblyRegularJointError(
                "Those two exact components already have an active "
                f"{joint_type} joint."
            )


def _validate_regular_spec(spec: RegularJointSpec) -> None:
    if REGULAR_JOINT_TYPE_INDICES.get(spec.joint_type) != spec.type_index:
        raise NativeAssemblyRegularJointError(
            "The requested regular joint type/index pair is unsupported."
        )
    names = tuple(item.name for item in spec.properties)
    if len(names) != len(set(names)) or set(names) != set(
        REGULAR_JOINT_PROPERTIES[spec.joint_type]
    ):
        raise NativeAssemblyRegularJointError(
            f"The {spec.joint_type} joint properties are incomplete or invalid."
        )
    for item in spec.properties:
        if item.name.startswith("Enable"):
            valid = type(item.value) is bool
        else:
            valid = isinstance(item.value, float)
        if not valid:
            raise NativeAssemblyRegularJointError(
                f"The {spec.joint_type} joint property {item.name} has the wrong type."
            )


def preflight_regular_joint(
    document: Any,
    spec: RegularJointSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
    preference_reader: Callable[[], bool] = _solve_on_creation,
) -> PreparedRegularJoint:
    """Resolve the complete regular-joint request without mutation."""

    if not isinstance(spec, RegularJointSpec):
        raise TypeError("spec must be a RegularJointSpec")
    _validate_regular_spec(spec)
    assembly = resolve_object(
        document,
        spec.assembly_ref,
        expected_types=("Assembly::AssemblyObject",),
    )
    active = active_reader(document)
    if not same_assembly(assembly, active):
        raise NativeAssemblyRegularJointError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    if not timeline_active(assembly) or not object_is_valid(assembly):
        raise NativeAssemblyRegularJointError(
            "The human-active Assembly is not active and valid in History."
        )
    components = assembly_components(assembly)
    if len(components) != spec.expected_component_count:
        raise NativeAssemblyRegularJointError(
            "The active Assembly component count changed; read current Assemble state and retry."
        )
    try:
        joint_group = require_joint_group(assembly)
        regular = active_regular_joints(joint_group)
    except NativeAssemblyJointGraphError as exc:
        raise NativeAssemblyRegularJointError(str(exc)) from exc
    grounded = active_grounded_joints(joint_group)
    if len(regular) != spec.expected_joint_count:
        raise NativeAssemblyRegularJointError(
            "The active Assembly joint count changed; read current Assemble state and retry."
        )
    if len(grounded) != spec.expected_grounded_count:
        raise NativeAssemblyRegularJointError(
            "The active Assembly grounded count changed; read current Assemble state and retry."
        )
    if bool(preference_reader()) is not spec.expected_solve_on_creation:
        raise NativeAssemblyRegularJointError(
            "The Solve during joint creation preference changed; read current Assemble state and retry."
        )
    _validate_grounded_graph(assembly, grounded)
    _validate_regular_graph(assembly, joint_group, regular)
    try:
        first = resolve_joint_connector(document, assembly, spec.first)
        second = resolve_joint_connector(document, assembly, spec.second)
    except NativeAssemblyJointConnectorError as exc:
        raise NativeAssemblyRegularJointError(str(exc)) from exc
    if first.component is second.component:
        raise NativeAssemblyRegularJointError(
            f"A {spec.joint_type} joint requires two distinct exact Assembly components."
        )
    _reject_duplicate_joint_pair(
        regular,
        first.component,
        second.component,
        spec.joint_type,
    )
    return PreparedRegularJoint(
        assembly=assembly,
        joint_group=joint_group,
        first=first,
        second=second,
        active_before=active,
        selection_before=selection_reader(document),
        regular_joints_before=regular,
        grounded_joints_before=grounded,
    )


def _copy_placement(value: Any) -> Any:
    try:
        import FreeCAD as App

        return App.Placement(value)
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return value


def _movable_components(assembly: Any) -> tuple[Any, ...]:
    try:
        import UtilsAssembly

        return tuple(UtilsAssembly.getMovablePartsWithin(assembly))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return ()


def _placement_map(assembly: Any) -> dict[Any, Any]:
    return {
        component: _copy_placement(component_placement(component))
        for component in _movable_components(assembly)
    }


def _new_document_objects(document: Any, before: tuple[Any, ...]) -> tuple[Any, ...]:
    previous = {id(obj) for obj in before}
    return tuple(obj for obj in tuple(document.Objects) if id(obj) not in previous)


def _connector_key(summary: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(summary["component"]["object_name"]),
        str(summary["element_path"]),
        str(summary["anchor_path"]),
    )


def _expected_connector_key(spec: JointConnectorSpec) -> tuple[str, str, str]:
    return (
        spec.component_ref.object_name,
        spec.element_path,
        spec.anchor_path,
    )


def _set_joint_properties(joint: Any, spec: RegularJointSpec) -> None:
    for item in spec.properties:
        if not hasattr(joint, item.name):
            raise NativeAssemblyRegularJointError(
                f"The native {spec.joint_type} joint lacks {item.name}."
            )
        try:
            setattr(joint, item.name, item.value)
        except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
            raise NativeAssemblyRegularJointError(
                f"The native {spec.joint_type} joint rejected {item.name}."
            ) from exc


def _joint_property_value(joint: Any, item: RegularJointPropertySpec) -> bool | float:
    try:
        value = getattr(joint, item.name)
        if type(item.value) is bool:
            return bool(value)
        return float(getattr(value, "Value", value))
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyRegularJointError(
            f"The native joint property {item.name} could not be read."
        ) from exc


def _verify_joint_properties(
    joint: Any,
    spec: RegularJointSpec,
) -> dict[str, bool | float]:
    result: dict[str, bool | float] = {}
    for item in spec.properties:
        actual = _joint_property_value(joint, item)
        if type(item.value) is bool:
            matches = actual is item.value
        else:
            matches = abs(float(actual) - item.value) <= 1.0e-9
        if not matches:
            raise NativeAssemblyRegularJointError(
                f"The native {spec.joint_type} joint changed {item.name}."
            )
        result[item.name] = actual
    return result


def apply_regular_joint(
    document: Any,
    spec: RegularJointSpec,
    *,
    joint_factory: Callable[[Any, Any, RegularJointSpec], Any] = _create_regular_joint,
) -> NativeMutationDraft:
    """Create one real regular joint and its exact connector state."""

    prepared = preflight_regular_joint(document, spec)
    before_objects = tuple(document.Objects)
    before_names = tuple(str(obj.Name) for obj in before_objects)
    placements_before = _placement_map(prepared.assembly)
    prepared.assembly.ensureIdentityPlacements()
    joint = joint_factory(prepared.assembly, prepared.joint_group, spec)
    if (
        joint is None
        or getattr(joint, "Document", None) is not document
        or joint not in list(getattr(prepared.joint_group, "Group", ()) or ())
        or str(getattr(joint, "JointType", "") or "") != spec.joint_type
    ):
        raise NativeAssemblyRegularJointError(
            f"The native {spec.joint_type}-joint factory returned the wrong object graph."
        )
    joint.Offset1 = _copy_placement(spec.first.offset)
    joint.Offset2 = _copy_placement(spec.second.offset)
    _set_joint_properties(joint, spec)
    try:
        joint.Proxy.setJointConnectors(
            joint,
            [prepared.first.reference, prepared.second.reference],
        )
        if spec.reverse:
            joint.Proxy.flipOnePart(joint)
        if spec.expected_solve_on_creation:
            prepared.assembly.solve()
    except Exception as exc:
        raise NativeAssemblyRegularJointError(
            f"The native {spec.joint_type}-joint connector operation failed."
        ) from exc
    placements_after = _placement_map(prepared.assembly)
    moved = tuple(
        component
        for component, before in placements_before.items()
        if component in placements_after
        and not placement_is_same(before, placements_after[component])
    )
    changed = (
        prepared.assembly,
        prepared.joint_group,
        prepared.first.component,
        prepared.second.component,
        *moved,
    )
    unique_changed = []
    seen: set[str] = set()
    for obj in changed:
        name = str(getattr(obj, "Name", "") or "")
        if name and name not in seen:
            seen.add(name)
            unique_changed.append(object_identity(obj))
    return NativeMutationDraft(
        value={
            "spec": spec,
            "assembly": prepared.assembly,
            "joint_group": prepared.joint_group,
            "joint": joint,
            "active_before": prepared.active_before,
            "selection_before": prepared.selection_before,
            "regular_joints_before": prepared.regular_joints_before,
            "grounded_joints_before": prepared.grounded_joints_before,
            "before_objects": before_objects,
            "before_names": before_names,
            "placements_before": placements_before,
            "moved": moved,
        },
        recompute_targets=(
            joint,
            prepared.first.component,
            prepared.second.component,
            *moved,
            prepared.joint_group,
            prepared.assembly,
        ),
        created=(object_identity(joint),),
        changed=tuple(unique_changed),
    )


def _joint_diagnostic(diagnostics: dict[str, Any], joint_name: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in list(diagnostics.get("joints") or [])
            if str(item.get("joint") or "") == joint_name
        ),
        None,
    )


def _verify_solver_result(
    spec: RegularJointSpec,
    joint: Any,
    diagnostics: dict[str, Any],
) -> None:
    if not spec.expected_solve_on_creation:
        return
    status = diagnostics.get("solver_status")
    if status == -6:
        return
    if status != 0:
        raise NativeAssemblyRegularJointError(
            f"The native solver rejected the new {spec.joint_type} joint."
        )
    joint_result = _joint_diagnostic(diagnostics, str(joint.Name))
    if joint_result is not None and joint_result.get("status") != "satisfied":
        raise NativeAssemblyRegularJointError(
            f"The new {spec.joint_type} joint is conflicting, redundant, or malformed."
        )


def verify_regular_joint(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
    view_checker: Callable[[Any], bool] = _regular_view_is_exact,
    proxy_checker: Callable[[Any], bool] = _regular_proxy_is_exact,
) -> dict[str, Any]:
    """Prove the exact regular-joint graph, connector, solver, and GUI invariants."""

    value = draft.value
    spec = value["spec"]
    assembly = value["assembly"]
    joint_group = value["joint_group"]
    joint = value["joint"]
    before_names = set(value["before_names"])
    if (
        document.getObject(str(assembly.Name)) is not assembly
        or document.getObject(str(joint_group.Name)) is not joint_group
        or document.getObject(str(joint.Name)) is not joint
        or not object_is_valid(assembly)
        or not object_is_valid(joint_group)
        or not object_is_valid(joint)
        or not same_assembly(value["active_before"], active_reader(document))
        or selection_reader(document) != value["selection_before"]
        or len(assembly_components(assembly)) != spec.expected_component_count
    ):
        raise NativeAssemblyRegularJointError(
            f"{spec.joint_type}-joint creation changed Assembly identity, "
            "validity, activation, selection, or components."
        )
    regular = active_regular_joints(joint_group)
    grounded = active_grounded_joints(joint_group)
    if (
        len(regular) != spec.expected_joint_count + 1
        or set(regular) != set(value["regular_joints_before"]) | {joint}
        or len(grounded) != spec.expected_grounded_count
        or set(grounded) != set(value["grounded_joints_before"])
    ):
        raise NativeAssemblyRegularJointError(
            f"{spec.joint_type}-joint creation changed the wrong active joint objects."
        )
    new_objects = _new_document_objects(document, value["before_objects"])
    after_names = {str(obj.Name) for obj in tuple(document.Objects)}
    if new_objects != (joint,) or after_names != before_names | {str(joint.Name)}:
        raise NativeAssemblyRegularJointError(
            f"{spec.joint_type}-joint creation added objects outside the exact native joint graph."
        )
    if (
        str(getattr(joint, "JointType", "") or "") != spec.joint_type
        or str(getattr(joint, "Label", "") or "") != spec.label
        or bool(getattr(joint, "Suppressed", False))
        or not timeline_active(joint)
        or str(getattr(joint, "VibeCADTimelineRole", "") or "") != "operation"
        or not proxy_checker(joint)
        or not view_checker(joint)
    ):
        raise NativeAssemblyRegularJointError(
            f"The created object is not one active native {spec.joint_type}-joint operation."
        )
    first = connector_summary(joint.Reference1, joint.Offset1)
    second = connector_summary(joint.Reference2, joint.Offset2)
    actual = {
        _connector_key(first): joint.Offset1,
        _connector_key(second): joint.Offset2,
    }
    expected = {
        _expected_connector_key(spec.first): spec.first.offset,
        _expected_connector_key(spec.second): spec.second.offset,
    }
    if set(actual) != set(expected) or any(
        not placement_is_same(actual[key], expected[key]) for key in expected
    ):
        raise NativeAssemblyRegularJointError(
            f"The native {spec.joint_type} joint did not preserve its exact "
            "connectors and offsets."
        )
    properties = _verify_joint_properties(joint, spec)
    diagnostics = solver_diagnostics(assembly)
    _verify_solver_result(spec, joint, diagnostics)
    moved = []
    for component, before in value["placements_before"].items():
        if document.getObject(str(component.Name)) is not component:
            raise NativeAssemblyRegularJointError(
                "A movable Assembly component disappeared during regular-joint creation."
            )
        after = component_placement(component)
        if not placement_is_same(before, after):
            moved.append(
                {
                    "component": object_reference(component),
                    "placement": placement_summary(after),
                }
            )
    return {
        "assembly": object_reference(assembly),
        "joint": object_reference(joint),
        "joint_type": spec.joint_type,
        "connectors": [first, second],
        "reverse": spec.reverse,
        "properties": properties,
        "component_count": len(assembly_components(assembly)),
        "grounded_count": len(grounded),
        "joint_count": len(regular),
        "moved_components": moved,
        "solver": diagnostics,
        "active_assembly_unchanged": True,
        "selection_unchanged": True,
    }
