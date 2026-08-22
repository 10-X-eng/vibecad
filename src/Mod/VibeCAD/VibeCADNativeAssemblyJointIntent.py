# SPDX-License-Identifier: LGPL-2.1-or-later

"""Translate small Assembly joint intent into exact guarded runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import VibeCADReferenceContracts as reference_contracts
from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyDistanceJoint import distance_mode_for_resolved
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyJointArguments import joint_connector
from VibeCADNativeAssemblyJointConnectors import (
    NativeAssemblyJointConnectorError,
    component_placement,
    placement_summary,
    resolve_joint_connector,
)
from VibeCADNativeAssemblyJointGraph import (
    NativeAssemblyJointGraphError,
    active_regular_joints,
    require_joint_group,
)
from VibeCADNativeAssemblyState import (
    NativeAssemblyStateError,
    read_active_assembly,
    same_assembly,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    NativeTargetError,
    resolve_object,
)


class NativeAssemblyJointIntentError(RuntimeError):
    """Assembly joint intent cannot be resolved against the live document."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class _JointState:
    assembly: Any
    assembly_ref: NativeObjectRef
    component_count: int
    grounded_count: int
    joint_count: int
    solve_on_creation: bool
    grounded_components: frozenset[Any]
    regular_joints: tuple[Any, ...]


_DEFAULT_LABELS = {
    "create_fixed": "Fixed Joint",
    "create_revolute": "Revolute Joint",
    "create_cylindrical": "Cylindrical Joint",
    "create_slider": "Slider Joint",
    "create_ball": "Ball Joint",
    "create_distance": "Distance Joint",
    "create_parallel": "Parallel Joint",
    "create_perpendicular": "Perpendicular Joint",
    "create_angle": "Angle Joint",
    "create_rack_pinion": "Rack and Pinion Joint",
    "create_screw": "Screw Joint",
    "create_belt": "Belt Joint",
    "create_gears": "Gears Joint",
}

_COUPLING_JOINT_TYPES = {
    "create_rack_pinion": ("Slider", "Revolute"),
    "create_screw": ("Slider", "Revolute"),
    "create_belt": ("Revolute", "Revolute"),
    "create_gears": ("Revolute", "Revolute"),
}


def _object_ref(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, str):
        raise NativeAssemblyJointIntentError(
            f"{field} must be an internal object name from the assembly state."
        )
    try:
        return NativeObjectRef(document_uid, value)
    except NativeTargetError as exc:
        raise NativeAssemblyJointIntentError(str(exc)) from exc


def _solve_on_creation() -> bool:
    try:
        import Preferences

        return bool(
            Preferences.preferences().GetBool("SolveInJointCreation", True)
        )
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return True


def _state(document: Any, document_uid: str) -> _JointState:
    try:
        active = read_active_assembly(document)
        if active is None:
            raise NativeAssemblyJointIntentError("No Assembly is active.")
        assembly_ref = NativeObjectRef(document_uid, str(active.Name))
        assembly = resolve_object(
            document,
            assembly_ref,
            expected_types=("Assembly::AssemblyObject",),
        )
        if not same_assembly(assembly, active):
            raise NativeAssemblyJointIntentError(
                "assembly must name the active assembly."
            )
        joint_group = require_joint_group(assembly)
        regular = active_regular_joints(joint_group)
        grounded = active_grounded_joints(joint_group)
        return _JointState(
            assembly=assembly,
            assembly_ref=assembly_ref,
            component_count=len(assembly_components(assembly)),
            grounded_count=len(grounded),
            joint_count=len(regular),
            solve_on_creation=_solve_on_creation(),
            grounded_components=frozenset(
                component
                for component in (
                    getattr(joint, "ObjectToGround", None) for joint in grounded
                )
                if component is not None
            ),
            regular_joints=tuple(regular),
        )
    except NativeAssemblyJointIntentError:
        raise
    except (
        NativeAssemblyJointGraphError,
        NativeAssemblyStateError,
        NativeTargetError,
    ) as exc:
        raise NativeAssemblyJointIntentError(str(exc)) from exc


def _placement(value: Any) -> dict[str, Any]:
    if value is None:
        translation = (0.0, 0.0, 0.0)
        axis = (0.0, 0.0, 1.0)
        angle = 0.0
    else:
        if not isinstance(value, Mapping) or set(value) != {
            "translation_mm",
            "rotation_axis",
            "rotation_degrees",
        }:
            raise NativeAssemblyJointIntentError(
                "offset must contain translation_mm, rotation_axis, and rotation_degrees."
            )
        translation = value["translation_mm"]
        axis = value["rotation_axis"]
        angle = value["rotation_degrees"]
        if (
            not isinstance(translation, list)
            or len(translation) != 3
            or not isinstance(axis, list)
            or len(axis) != 3
        ):
            raise NativeAssemblyJointIntentError(
                "offset vectors must contain exactly three numbers."
            )
    return {
        "origin_mm": dict(zip("xyz", translation, strict=True)),
        "rotation": {
            "axis": dict(zip("xyz", axis, strict=True)),
            "angle_degrees": angle,
        },
    }


def _connector(
    document: Any,
    document_uid: str,
    value: Any,
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeAssemblyJointIntentError(
            f"{field} must identify geometry or the component origin."
        )
    fields = set(value)
    geometry_endpoint = (
        {"component", "element"} <= fields
        and fields <= {"component", "element", "anchor", "offset"}
        and isinstance(value.get("element"), str)
        and bool(value["element"])
    )
    interface_endpoint = (
        {"component", "interface"} <= fields
        and fields <= {"component", "interface", "offset"}
        and isinstance(value.get("interface"), str)
        and bool(value["interface"])
    )
    if geometry_endpoint == interface_endpoint:
        raise NativeAssemblyJointIntentError(
            f"{field} requires component with one element or interface."
        )
    origin = geometry_endpoint and value["element"].casefold() == "origin"
    if geometry_endpoint and origin and "anchor" in fields:
        raise NativeAssemblyJointIntentError(
            f"{field}.anchor does not apply to the component origin."
        )
    component_ref = _object_ref(
        document_uid,
        value["component"],
        f"{field}.component",
    )
    try:
        component = resolve_object(document, component_ref)
        expected_placement = placement_summary(component_placement(component))
    except (NativeAssemblyJointConnectorError, NativeTargetError) as exc:
        raise NativeAssemblyJointIntentError(str(exc)) from exc
    offset = _placement(value.get("offset"))
    if interface_endpoint:
        try:
            interface = reference_contracts.resolve_component_interface(
                component, value["interface"]
            )
        except reference_contracts.ReferenceContractError as exc:
            raise NativeAssemblyJointIntentError(str(exc)) from exc
        subelements = list(interface.get("subelements") or [])
        if len(subelements) > 1:
            raise NativeAssemblyJointIntentError(
                f"{field}.interface must resolve to one connector."
            )
        element = str(subelements[0]) if subelements else ""
        anchor = element
        if dict(interface.get("selection") or {}).get("type") == "frame":
            try:
                from VibeCADNativePartPrimitives import part_placement_from_mapping

                frame = reference_contracts.connector_frame_placement(
                    interface.get("connector_frame")
                )
                requested = part_placement_from_mapping(offset)
                offset = placement_summary(frame.multiply(requested))
            except (
                reference_contracts.ReferenceContractError,
                NativeAssemblyJointConnectorError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                raise NativeAssemblyJointIntentError(
                    f"{field}.interface has an invalid connector frame."
                ) from exc
    else:
        element = "" if origin else value["element"]
        anchor = value.get("anchor", element)
    if not isinstance(element, str) or not isinstance(anchor, str):
        raise NativeAssemblyJointIntentError(
            f"{field} element and anchor must be geometry paths."
        )
    return {
        "component": {"object_name": component_ref.object_name},
        "element_path": element,
        "anchor_path": anchor,
        "offset": offset,
        "expected_component_placement": expected_placement,
    }


def _coupling_target_connector(
    document: Any,
    document_uid: str,
    state: _JointState,
    value: Any,
    field: str,
    expected_joint_type: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"joint", "component"}:
        raise NativeAssemblyJointIntentError(
            f"{field} must contain one joint and one component from "
            "assembly.component_joints."
        )
    joint_ref = _object_ref(document_uid, value["joint"], f"{field}.joint")
    component_ref = _object_ref(
        document_uid,
        value["component"],
        f"{field}.component",
    )
    try:
        joint = resolve_object(document, joint_ref)
        component = resolve_object(document, component_ref)
    except NativeTargetError as exc:
        raise NativeAssemblyJointIntentError(str(exc)) from exc
    if (
        joint not in state.regular_joints
        or str(getattr(joint, "JointType", "") or "") != expected_joint_type
    ):
        raise NativeAssemblyJointIntentError(
            f"{field}.joint must be one active {expected_joint_type} joint."
        )
    matches = []
    for side in (1, 2):
        try:
            reference = getattr(joint, f"Reference{side}")
            if reference[0] is component:
                matches.append((side, reference))
        except (AttributeError, IndexError, ReferenceError, TypeError):
            continue
    if len(matches) != 1:
        raise NativeAssemblyJointIntentError(
            f"{field}.component must identify one side of {field}.joint."
        )
    side, reference = matches[0]
    try:
        paths = tuple(str(path) for path in reference[1])
        if len(paths) != 2:
            raise ValueError
        offset = getattr(joint, f"Offset{side}")
        expected_placement = component_placement(component)
        return {
            "component": {"object_name": component_ref.object_name},
            "element_path": paths[0],
            "anchor_path": paths[1],
            "offset": placement_summary(offset),
            "expected_component_placement": placement_summary(expected_placement),
        }
    except (
        AttributeError,
        NativeAssemblyJointConnectorError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        raise NativeAssemblyJointIntentError(
            f"{field} references a malformed joint connector."
        ) from exc


def _expected_state(state: _JointState) -> dict[str, Any]:
    return {
        "expected_component_count": state.component_count,
        "expected_grounded_count": state.grounded_count,
        "expected_joint_count": state.joint_count,
        "expected_solve_on_creation": state.solve_on_creation,
    }


def _legacy_limits(
    value: Any,
    minimum: str,
    maximum: str,
    unit: str,
    *,
    allowed: frozenset[str] | None = None,
) -> dict[str, Any]:
    limits = {} if value is None else value
    supported = allowed or frozenset({minimum, maximum})
    if not isinstance(limits, Mapping) or not set(limits) <= supported:
        raise NativeAssemblyJointIntentError("limits contain unsupported fields.")
    return {
        "minimum": {
            "enabled": minimum in limits,
            unit: limits.get(minimum, 0.0),
        },
        "maximum": {
            "enabled": maximum in limits,
            unit: limits.get(maximum, 0.0),
        },
    }


def _distance_mode(
    document: Any,
    state: _JointState,
    first: dict[str, Any],
    second: dict[str, Any],
) -> str:
    try:
        first_spec = joint_connector(
            state.assembly_ref.document_uid,
            first,
            "first",
            NativeAssemblyJointIntentError,
        )
        second_spec = joint_connector(
            state.assembly_ref.document_uid,
            second,
            "second",
            NativeAssemblyJointIntentError,
        )
        first_resolved = resolve_joint_connector(document, state.assembly, first_spec)
        second_resolved = resolve_joint_connector(document, state.assembly, second_spec)
        mode, _swap = distance_mode_for_resolved(first_resolved, second_resolved)
        return mode
    except NativeAssemblyJointConnectorError as exc:
        raise NativeAssemblyJointIntentError(str(exc)) from exc


def expand_joint_intent(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture runtime-owned facts for one already schema-validated request."""

    state = _state(document, document_uid)
    assembly = {"object_name": state.assembly_ref.object_name}
    if operation in {"set_grounded", "set_movable"}:
        targets = []
        for index, name in enumerate(values.get("components", ())):
            reference = _object_ref(document_uid, name, f"components[{index}]")
            try:
                component = resolve_object(document, reference)
            except NativeTargetError as exc:
                raise NativeAssemblyJointIntentError(str(exc)) from exc
            targets.append(
                {
                    "component": {"object_name": reference.object_name},
                    "expected_grounded": component in state.grounded_components,
                }
            )
        return {
            "assembly": assembly,
            "targets": targets,
            "grounded": operation == "set_grounded",
            "expected_component_count": state.component_count,
            "expected_grounded_count": state.grounded_count,
        }

    target_types = _COUPLING_JOINT_TYPES.get(operation)
    provider_coupling = bool(target_types and "first_joint" in values)
    if provider_coupling:
        first = _coupling_target_connector(
            document,
            document_uid,
            state,
            {
                "joint": values["first_joint"],
                "component": values["first_component"],
            },
            "first",
            target_types[0],
        )
        second = _coupling_target_connector(
            document,
            document_uid,
            state,
            {
                "joint": values["second_joint"],
                "component": values["second_component"],
            },
            "second",
            target_types[1],
        )
    else:
        first = _connector(document, document_uid, values.get("first"), "first")
        second = _connector(document, document_uid, values.get("second"), "second")
    expanded: dict[str, Any] = {
        "assembly": assembly,
        "first": first,
        "second": second,
        "label": values.get("label", _DEFAULT_LABELS[operation]),
        **_expected_state(state),
    }
    if operation in {
        "create_fixed",
        "create_revolute",
        "create_cylindrical",
        "create_slider",
        "create_distance",
        "create_parallel",
    }:
        expanded["reverse"] = values.get("reverse", False)
    if operation == "create_revolute":
        expanded["limits"] = _legacy_limits(
            values.get("limits"),
            "minimum_degrees",
            "maximum_degrees",
            "degrees",
        )
    elif operation == "create_slider":
        expanded["limits"] = _legacy_limits(
            values.get("limits"),
            "minimum_mm",
            "maximum_mm",
            "mm",
        )
    elif operation == "create_cylindrical":
        limits = values.get("limits")
        cylindrical_fields = frozenset(
            {
                "minimum_mm",
                "maximum_mm",
                "minimum_degrees",
                "maximum_degrees",
            }
        )
        expanded["limits"] = {
            "length": _legacy_limits(
                limits,
                "minimum_mm",
                "maximum_mm",
                "mm",
                allowed=cylindrical_fields,
            ),
            "angle": _legacy_limits(
                limits,
                "minimum_degrees",
                "maximum_degrees",
                "degrees",
                allowed=cylindrical_fields,
            ),
        }
    elif operation == "create_distance":
        expanded["distance_mm"] = values["distance_mm"]
        expanded["expected_distance_mode"] = _distance_mode(
            document,
            state,
            first,
            second,
        )
    elif operation == "create_angle":
        expanded["angle_degrees"] = values["angle_degrees"]
    elif operation == "create_rack_pinion":
        expanded["rack_connector"] = expanded.pop("first")
        expanded["pinion_connector"] = expanded.pop("second")
        expanded["rack_slider_joint"] = {
            "object_name": (
                values["first_joint"]
                if provider_coupling
                else values["rack_slider_joint"]
            )
        }
        expanded["pinion_revolute_joint"] = {
            "object_name": (
                values["second_joint"]
                if provider_coupling
                else values["pinion_revolute_joint"]
            )
        }
        expanded["pitch_radius_mm"] = values["pitch_radius_mm"]
    elif operation == "create_screw":
        expanded["slider_connector"] = expanded.pop("first")
        expanded["screw_connector"] = expanded.pop("second")
        expanded["slider_joint"] = {
            "object_name": (
                values["first_joint"]
                if provider_coupling
                else values["slider_joint"]
            )
        }
        expanded["screw_revolute_joint"] = {
            "object_name": (
                values["second_joint"]
                if provider_coupling
                else values["screw_revolute_joint"]
            )
        }
        expanded["thread_pitch_mm"] = values["thread_pitch_mm"]
    elif operation in {"create_belt", "create_gears"}:
        noun = "pulley" if operation == "create_belt" else "gear"
        expanded[f"first_{noun}_connector"] = expanded.pop("first")
        expanded[f"second_{noun}_connector"] = expanded.pop("second")
        expanded["first_revolute_joint"] = {
            "object_name": (
                values["first_joint"]
                if provider_coupling
                else values["first_revolute_joint"]
            )
        }
        expanded["second_revolute_joint"] = {
            "object_name": (
                values["second_joint"]
                if provider_coupling
                else values["second_revolute_joint"]
            )
        }
        expanded["radius1_mm"] = values["radius1_mm"]
        expanded["radius2_mm"] = values["radius2_mm"]
    return expanded
