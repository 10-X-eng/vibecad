# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Gears coupling over two explicit Revolute prerequisites."""

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


MIN_GEAR_RADIUS_MM = 1.0e-7
MAX_GEAR_RADIUS_MM = 1_000_000.0


class NativeAssemblyGearJointError(RuntimeError):
    """An exact Gears request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_GEAR_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class GearJointSpec:
    assembly_ref: NativeObjectRef
    first_gear_connector: JointConnectorSpec
    second_gear_connector: JointConnectorSpec
    first_revolute_joint_ref: NativeObjectRef
    second_revolute_joint_ref: NativeObjectRef
    label: str
    radius1_mm: float
    radius2_mm: float
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


@dataclass(frozen=True, slots=True)
class PreparedGearJoint:
    regular: PreparedRegularJoint
    first_revolute_joint: Any
    second_revolute_joint: Any
    first_revolute_side: int
    second_revolute_side: int


def gear_radius_mm(value: Any, field: str) -> float:
    """Return one finite, strictly positive, bounded gear radius."""

    if isinstance(value, bool):
        raise NativeAssemblyGearJointError(f"{field} must be a positive radius in mm.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyGearJointError(
            f"{field} must be a positive radius in mm."
        ) from exc
    if not (
        math.isfinite(number) and MIN_GEAR_RADIUS_MM <= number <= MAX_GEAR_RADIUS_MM
    ):
        raise NativeAssemblyGearJointError(
            f"{field} must be from {MIN_GEAR_RADIUS_MM:g} through "
            f"{MAX_GEAR_RADIUS_MM:g} mm."
        )
    return number


def _regular_spec(spec: GearJointSpec) -> RegularJointSpec:
    if not isinstance(spec, GearJointSpec):
        raise TypeError("spec must be a GearJointSpec")
    radius1 = gear_radius_mm(spec.radius1_mm, "radius1_mm")
    radius2 = gear_radius_mm(spec.radius2_mm, "radius2_mm")
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first_gear_connector,
        second=spec.second_gear_connector,
        joint_type="Gears",
        type_index=11,
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
) -> Any:
    try:
        return resolve_object(document, reference)
    except NativeTargetError as exc:
        raise NativeAssemblyGearJointError(
            f"The exact {field} prerequisite changed; read current Assemble state "
            "and retry."
        ) from exc


def _validate_dependencies(
    prepared: PreparedRegularJoint,
    spec: GearJointSpec,
    first_revolute: Any,
    second_revolute: Any,
) -> tuple[int, int]:
    active = set(prepared.regular_joints_before)
    if (
        first_revolute is second_revolute
        or first_revolute not in active
        or second_revolute not in active
        or str(getattr(first_revolute, "JointType", "") or "") != "Revolute"
        or str(getattr(second_revolute, "JointType", "") or "") != "Revolute"
    ):
        raise NativeAssemblyGearJointError(
            "Gears coupling requires two distinct exact active Revolute prerequisite "
            "joints in the human-active Assembly."
        )
    first_gear = prepared.first.component
    second_gear = prepared.second.component
    grounded = {
        getattr(joint, "ObjectToGround", None)
        for joint in prepared.grounded_joints_before
    }
    if first_gear in grounded or second_gear in grounded:
        raise NativeAssemblyGearJointError(
            "Both gear components must retain their Revolute degrees of freedom "
            "rather than being grounded."
        )
    if second_gear in joint_components(
        first_revolute
    ) or first_gear in joint_components(second_revolute):
        raise NativeAssemblyGearJointError(
            "The two Revolute prerequisites must constrain distinct rotating gear "
            "components."
        )
    first_side = matching_spec_side(
        first_revolute,
        first_gear,
        spec.first_gear_connector,
    )
    second_side = matching_spec_side(
        second_revolute,
        second_gear,
        spec.second_gear_connector,
    )
    if not first_side or not second_side:
        raise NativeAssemblyGearJointError(
            "Gear connectors must exactly reuse the named Revolute joint coordinate "
            "systems."
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


def gears_dependency_summary(
    joint: Any,
    active_joints: Iterable[Any],
) -> dict[str, Any] | None:
    """Derive the two exact Revolute prerequisites from persisted connectors."""

    candidates = tuple(item for item in active_joints if item is not joint)
    first = _matching_revolute_sides(joint, 1, candidates)
    second = _matching_revolute_sides(joint, 2, candidates)
    if len(first) != 1 or len(second) != 1 or first[0][0] is second[0][0]:
        return None
    return {
        "first_revolute_joint": object_reference(first[0][0]),
        "second_revolute_joint": object_reference(second[0][0]),
    }


def _gear_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblyGearJointError:
    return NativeAssemblyGearJointError(str(exc))


def preflight_gear_joint(
    document: Any,
    spec: GearJointSpec,
    **kwargs: Any,
) -> PreparedGearJoint:
    try:
        regular = preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _gear_failure(exc) from exc
    first_revolute = _resolve_dependency(
        document,
        spec.first_revolute_joint_ref,
        "first gear Revolute joint",
    )
    second_revolute = _resolve_dependency(
        document,
        spec.second_revolute_joint_ref,
        "second gear Revolute joint",
    )
    first_side, second_side = _validate_dependencies(
        regular,
        spec,
        first_revolute,
        second_revolute,
    )
    return PreparedGearJoint(
        regular,
        first_revolute,
        second_revolute,
        first_side,
        second_side,
    )


def apply_gear_joint(
    document: Any,
    spec: GearJointSpec,
    *,
    joint_factory: Callable[[Any, Any, GearJointSpec], Any] | None = None,
) -> NativeMutationDraft:
    prepared = preflight_gear_joint(document, spec)
    kwargs: dict[str, Any] = {}
    if joint_factory is not None:
        kwargs["joint_factory"] = lambda assembly, joint_group, _spec: joint_factory(
            assembly,
            joint_group,
            spec,
        )
    try:
        draft = apply_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _gear_failure(exc) from exc
    draft.value["first_revolute_joint"] = prepared.first_revolute_joint
    draft.value["second_revolute_joint"] = prepared.second_revolute_joint
    draft.value["first_revolute_side"] = prepared.first_revolute_side
    draft.value["second_revolute_side"] = prepared.second_revolute_side
    draft.value["gear_spec"] = spec
    return draft


def verify_gear_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _gear_failure(exc) from exc
    value = draft.value
    spec = value.get("gear_spec")
    joint = value["joint"]
    first_revolute = value["first_revolute_joint"]
    second_revolute = value["second_revolute_joint"]
    if (
        not isinstance(spec, GearJointSpec)
        or document.getObject(str(first_revolute.Name)) is not first_revolute
        or document.getObject(str(second_revolute.Name)) is not second_revolute
        or str(getattr(first_revolute, "JointType", "") or "") != "Revolute"
        or str(getattr(second_revolute, "JointType", "") or "") != "Revolute"
        or not sides_equal(joint, 1, first_revolute, value["first_revolute_side"])
        or not sides_equal(joint, 2, second_revolute, value["second_revolute_side"])
    ):
        raise NativeAssemblyGearJointError(
            "The native Gears joint changed its exact prerequisite graph."
        )
    properties = result.pop("properties")
    radius1 = float(properties["Distance"])
    radius2 = float(properties["Distance2"])
    connectors = result.pop("connectors")
    result.pop("reverse")
    result["first_gear_connector"] = connectors[0]
    result["second_gear_connector"] = connectors[1]
    result["first_revolute_joint"] = object_reference(first_revolute)
    result["second_revolute_joint"] = object_reference(second_revolute)
    result["radius1_mm"] = radius1
    result["radius2_mm"] = radius2
    result["second_rotation_per_first_rotation"] = -(radius1 / radius2)
    result["rotation_direction"] = "opposite"
    return result
