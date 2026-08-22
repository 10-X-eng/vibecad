# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact mechanics for two-Revolute rotational couplings."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Iterable

from VibeCADNativeAssemblyCoupledJoint import (
    joint_components,
    matching_spec_side,
    sides_equal,
)
from VibeCADNativeAssemblyJointConnectors import JointConnectorSpec
from VibeCADNativeAssemblyRegularJoint import (
    NativeAssemblyRegularJointError,
    PreparedRegularJoint,
    RegularJointPropertySpec,
    RegularJointSpec,
    apply_regular_joint,
    preflight_regular_joint,
    verify_regular_joint,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    NativeTargetError,
    object_reference,
    resolve_object,
)


MIN_COUPLING_RADIUS_MM = 1.0e-7
MAX_COUPLING_RADIUS_MM = 1_000_000.0
MIN_COUPLING_AXIS_SEPARATION_MM = 1.0e-7


@dataclass(frozen=True, slots=True)
class RotationCouplingContract:
    joint_type: str
    type_index: int
    coupling_label: str
    connector_label: str
    component_noun: str
    first_connector_result_key: str
    second_connector_result_key: str
    rotation_multiplier: float
    rotation_direction: str


@dataclass(frozen=True, slots=True)
class PreparedRotationCoupling:
    regular: PreparedRegularJoint
    first_revolute_joint: Any
    second_revolute_joint: Any
    first_revolute_side: int
    second_revolute_side: int


def positive_coupling_radius_mm(
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> float:
    """Return one finite, strictly positive radius from the human task range."""

    if isinstance(value, bool):
        raise error_type(f"{field} must be a positive radius in mm.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise error_type(f"{field} must be a positive radius in mm.") from exc
    if not (
        math.isfinite(number)
        and MIN_COUPLING_RADIUS_MM <= number <= MAX_COUPLING_RADIUS_MM
    ):
        raise error_type(
            f"{field} must be from {MIN_COUPLING_RADIUS_MM:g} through "
            f"{MAX_COUPLING_RADIUS_MM:g} mm."
        )
    return number


def regular_rotation_coupling_spec(
    spec: Any,
    first_connector: JointConnectorSpec,
    second_connector: JointConnectorSpec,
    contract: RotationCouplingContract,
    error_type: type[RuntimeError],
) -> RegularJointSpec:
    """Translate one exact rotational coupling into the human joint properties."""

    radius1 = positive_coupling_radius_mm(spec.radius1_mm, "radius1_mm", error_type)
    radius2 = positive_coupling_radius_mm(spec.radius2_mm, "radius2_mm", error_type)
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=first_connector,
        second=second_connector,
        joint_type=contract.joint_type,
        type_index=contract.type_index,
        label=spec.label,
        reverse=False,
        properties=(
            RegularJointPropertySpec("Distance", radius1),
            RegularJointPropertySpec("Distance2", radius2),
        ),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _resolve_dependency(
    document: Any,
    reference: NativeObjectRef,
    field: str,
    error_type: type[RuntimeError],
) -> Any:
    try:
        return resolve_object(document, reference)
    except NativeTargetError as exc:
        raise error_type(
            f"The exact {field} prerequisite changed; read current Assemble state "
            "and retry."
        ) from exc


def rotation_coupling_axis_separation_mm(
    first: Any,
    second: Any,
) -> float | None:
    """Return native global connector-origin separation for real resolved inputs."""

    required = ("local_frame", "reference")
    if any(not hasattr(first, name) for name in required) or any(
        not hasattr(second, name) for name in required
    ):
        return None
    import UtilsAssembly

    placements = (
        UtilsAssembly.getJcsGlobalPlc(first.local_frame, first.reference),
        UtilsAssembly.getJcsGlobalPlc(second.local_frame, second.reference),
    )
    origins = []
    for placement in placements:
        base = placement.Base
        origin = (float(base.x), float(base.y), float(base.z))
        if not all(math.isfinite(value) for value in origin):
            raise ValueError("A coupling connector has a non-finite global origin.")
        origins.append(origin)
    return math.sqrt(
        sum(
            (first_value - second_value) ** 2
            for first_value, second_value in zip(origins[0], origins[1], strict=True)
        )
    )


def validate_rotation_coupling_dependencies(
    prepared: PreparedRegularJoint,
    first_connector: JointConnectorSpec,
    second_connector: JointConnectorSpec,
    first_revolute: Any,
    second_revolute: Any,
    contract: RotationCouplingContract,
    error_type: type[RuntimeError],
) -> tuple[int, int]:
    """Require two distinct live Revolute joints and their exact rotating sides."""

    active = set(prepared.regular_joints_before)
    if (
        first_revolute is second_revolute
        or first_revolute not in active
        or second_revolute not in active
        or str(getattr(first_revolute, "JointType", "") or "") != "Revolute"
        or str(getattr(second_revolute, "JointType", "") or "") != "Revolute"
    ):
        raise error_type(
            f"{contract.coupling_label} coupling requires two distinct exact active "
            "Revolute prerequisite joints in the human-active Assembly."
        )
    first_component = prepared.first.component
    second_component = prepared.second.component
    grounded = {
        getattr(joint, "ObjectToGround", None)
        for joint in prepared.grounded_joints_before
    }
    if first_component in grounded or second_component in grounded:
        raise error_type(
            f"Both {contract.component_noun} components must retain their Revolute "
            "degrees of freedom rather than being grounded."
        )
    if second_component in joint_components(
        first_revolute
    ) or first_component in joint_components(second_revolute):
        raise error_type(
            "The two Revolute prerequisites must constrain distinct rotating "
            f"{contract.component_noun} components."
        )
    first_side = matching_spec_side(
        first_revolute,
        first_component,
        first_connector,
    )
    second_side = matching_spec_side(
        second_revolute,
        second_component,
        second_connector,
    )
    if not first_side or not second_side:
        raise error_type(
            f"{contract.connector_label} connectors must exactly reuse the named "
            "Revolute joint coordinate systems."
        )
    try:
        axis_separation = rotation_coupling_axis_separation_mm(
            prepared.first,
            prepared.second,
        )
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise error_type(
            f"{contract.connector_label} connector global axes could not be resolved."
        ) from exc
    if (
        axis_separation is not None
        and axis_separation <= MIN_COUPLING_AXIS_SEPARATION_MM
    ):
        raise error_type(
            f"{contract.connector_label} connector axes are coincident; give the "
            "occurrences distinct initial placements before creating the coupling."
        )
    return first_side, second_side


def _matching_revolute_sides(
    joint: Any,
    joint_side: int,
    candidates: Iterable[Any],
) -> tuple[tuple[Any, int], ...]:
    return tuple(
        (candidate, candidate_side)
        for candidate in candidates
        if str(getattr(candidate, "JointType", "") or "") == "Revolute"
        for candidate_side in (1, 2)
        if sides_equal(joint, joint_side, candidate, candidate_side)
    )


def rotation_coupling_dependency_summary(
    joint: Any,
    active_joints: Iterable[Any],
) -> dict[str, Any] | None:
    """Derive two exact Revolute prerequisites from persisted connector identity."""

    candidates = tuple(item for item in active_joints if item is not joint)
    first = _matching_revolute_sides(joint, 1, candidates)
    second = _matching_revolute_sides(joint, 2, candidates)
    if len(first) != 1 or len(second) != 1 or first[0][0] is second[0][0]:
        return None
    return {
        "first_revolute_joint": object_reference(first[0][0]),
        "second_revolute_joint": object_reference(second[0][0]),
    }


def preflight_rotation_coupling(
    document: Any,
    spec: Any,
    *,
    first_connector: JointConnectorSpec,
    second_connector: JointConnectorSpec,
    first_revolute_joint_ref: NativeObjectRef,
    second_revolute_joint_ref: NativeObjectRef,
    first_dependency_label: str,
    second_dependency_label: str,
    contract: RotationCouplingContract,
    error_type: type[RuntimeError],
    **kwargs: Any,
) -> PreparedRotationCoupling:
    regular_spec = regular_rotation_coupling_spec(
        spec,
        first_connector,
        second_connector,
        contract,
        error_type,
    )
    try:
        regular = preflight_regular_joint(document, regular_spec, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise error_type(str(exc)) from exc
    first_revolute = _resolve_dependency(
        document,
        first_revolute_joint_ref,
        first_dependency_label,
        error_type,
    )
    second_revolute = _resolve_dependency(
        document,
        second_revolute_joint_ref,
        second_dependency_label,
        error_type,
    )
    first_side, second_side = validate_rotation_coupling_dependencies(
        regular,
        first_connector,
        second_connector,
        first_revolute,
        second_revolute,
        contract,
        error_type,
    )
    return PreparedRotationCoupling(
        regular,
        first_revolute,
        second_revolute,
        first_side,
        second_side,
    )


def apply_rotation_coupling(
    document: Any,
    spec: Any,
    *,
    first_connector: JointConnectorSpec,
    second_connector: JointConnectorSpec,
    first_revolute_joint_ref: NativeObjectRef,
    second_revolute_joint_ref: NativeObjectRef,
    first_dependency_label: str,
    second_dependency_label: str,
    contract: RotationCouplingContract,
    error_type: type[RuntimeError],
    joint_factory: Callable[[Any, Any, Any], Any] | None = None,
) -> NativeMutationDraft:
    prepared = preflight_rotation_coupling(
        document,
        spec,
        first_connector=first_connector,
        second_connector=second_connector,
        first_revolute_joint_ref=first_revolute_joint_ref,
        second_revolute_joint_ref=second_revolute_joint_ref,
        first_dependency_label=first_dependency_label,
        second_dependency_label=second_dependency_label,
        contract=contract,
        error_type=error_type,
    )
    regular_spec = regular_rotation_coupling_spec(
        spec,
        first_connector,
        second_connector,
        contract,
        error_type,
    )
    kwargs: dict[str, Any] = {}
    if joint_factory is not None:
        kwargs["joint_factory"] = lambda assembly, joint_group, _spec: joint_factory(
            assembly,
            joint_group,
            spec,
        )
    try:
        draft = apply_regular_joint(document, regular_spec, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise error_type(str(exc)) from exc
    draft.value["first_revolute_joint"] = prepared.first_revolute_joint
    draft.value["second_revolute_joint"] = prepared.second_revolute_joint
    draft.value["first_revolute_side"] = prepared.first_revolute_side
    draft.value["second_revolute_side"] = prepared.second_revolute_side
    draft.value["rotation_coupling_spec"] = spec
    return draft


def verify_rotation_coupling(
    document: Any,
    draft: NativeMutationDraft,
    *,
    spec_type: type,
    contract: RotationCouplingContract,
    error_type: type[RuntimeError],
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise error_type(str(exc)) from exc
    value = draft.value
    spec = value.get("rotation_coupling_spec")
    joint = value["joint"]
    first_revolute = value["first_revolute_joint"]
    second_revolute = value["second_revolute_joint"]
    if (
        not isinstance(spec, spec_type)
        or document.getObject(str(first_revolute.Name)) is not first_revolute
        or document.getObject(str(second_revolute.Name)) is not second_revolute
        or str(getattr(first_revolute, "JointType", "") or "") != "Revolute"
        or str(getattr(second_revolute, "JointType", "") or "") != "Revolute"
        or not sides_equal(joint, 1, first_revolute, value["first_revolute_side"])
        or not sides_equal(joint, 2, second_revolute, value["second_revolute_side"])
    ):
        raise error_type(
            f"The native {contract.joint_type} joint changed its exact prerequisite "
            "graph."
        )
    properties = result.pop("properties")
    radius1 = float(properties["Distance"])
    radius2 = float(properties["Distance2"])
    connectors = result.pop("connectors")
    result.pop("reverse")
    result[contract.first_connector_result_key] = connectors[0]
    result[contract.second_connector_result_key] = connectors[1]
    result["first_revolute_joint"] = object_reference(first_revolute)
    result["second_revolute_joint"] = object_reference(second_revolute)
    result["radius1_mm"] = radius1
    result["radius2_mm"] = radius2
    result["second_rotation_per_first_rotation"] = (
        contract.rotation_multiplier * radius1 / radius2
    )
    result["rotation_direction"] = contract.rotation_direction
    return result
