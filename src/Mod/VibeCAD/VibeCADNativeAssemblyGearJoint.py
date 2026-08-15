# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Gears coupling over two explicit Revolute prerequisites."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from VibeCADNativeAssemblyJointConnectors import JointConnectorSpec
from VibeCADNativeAssemblyRegularJoint import PreparedRegularJoint, RegularJointSpec
from VibeCADNativeAssemblyRotationCoupling import (
    MAX_COUPLING_RADIUS_MM,
    MIN_COUPLING_RADIUS_MM,
    PreparedRotationCoupling,
    RotationCouplingContract,
    apply_rotation_coupling,
    positive_coupling_radius_mm,
    preflight_rotation_coupling,
    regular_rotation_coupling_spec,
    rotation_coupling_dependency_summary,
    validate_rotation_coupling_dependencies,
    verify_rotation_coupling,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef


MIN_GEAR_RADIUS_MM = MIN_COUPLING_RADIUS_MM
MAX_GEAR_RADIUS_MM = MAX_COUPLING_RADIUS_MM


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


PreparedGearJoint = PreparedRotationCoupling


_GEAR_CONTRACT = RotationCouplingContract(
    joint_type="Gears",
    type_index=11,
    coupling_label="Gears",
    connector_label="Gear",
    component_noun="gear",
    first_connector_result_key="first_gear_connector",
    second_connector_result_key="second_gear_connector",
    rotation_multiplier=-1.0,
    rotation_direction="opposite",
)


def gear_radius_mm(value: Any, field: str) -> float:
    return positive_coupling_radius_mm(value, field, NativeAssemblyGearJointError)


def _regular_spec(spec: GearJointSpec) -> RegularJointSpec:
    if not isinstance(spec, GearJointSpec):
        raise TypeError("spec must be a GearJointSpec")
    return regular_rotation_coupling_spec(
        spec,
        spec.first_gear_connector,
        spec.second_gear_connector,
        _GEAR_CONTRACT,
        NativeAssemblyGearJointError,
    )


def _validate_dependencies(
    prepared: PreparedRegularJoint,
    spec: GearJointSpec,
    first_revolute: Any,
    second_revolute: Any,
) -> tuple[int, int]:
    return validate_rotation_coupling_dependencies(
        prepared,
        spec.first_gear_connector,
        spec.second_gear_connector,
        first_revolute,
        second_revolute,
        _GEAR_CONTRACT,
        NativeAssemblyGearJointError,
    )


def gears_dependency_summary(
    joint: Any,
    active_joints: Iterable[Any],
) -> dict[str, Any] | None:
    return rotation_coupling_dependency_summary(joint, active_joints)


def preflight_gear_joint(
    document: Any,
    spec: GearJointSpec,
    **kwargs: Any,
) -> PreparedGearJoint:
    if not isinstance(spec, GearJointSpec):
        raise TypeError("spec must be a GearJointSpec")
    return preflight_rotation_coupling(
        document,
        spec,
        first_connector=spec.first_gear_connector,
        second_connector=spec.second_gear_connector,
        first_revolute_joint_ref=spec.first_revolute_joint_ref,
        second_revolute_joint_ref=spec.second_revolute_joint_ref,
        first_dependency_label="first gear Revolute joint",
        second_dependency_label="second gear Revolute joint",
        contract=_GEAR_CONTRACT,
        error_type=NativeAssemblyGearJointError,
        **kwargs,
    )


def apply_gear_joint(
    document: Any,
    spec: GearJointSpec,
    *,
    joint_factory: Callable[[Any, Any, GearJointSpec], Any] | None = None,
) -> NativeMutationDraft:
    return apply_rotation_coupling(
        document,
        spec,
        first_connector=spec.first_gear_connector,
        second_connector=spec.second_gear_connector,
        first_revolute_joint_ref=spec.first_revolute_joint_ref,
        second_revolute_joint_ref=spec.second_revolute_joint_ref,
        first_dependency_label="first gear Revolute joint",
        second_dependency_label="second gear Revolute joint",
        contract=_GEAR_CONTRACT,
        error_type=NativeAssemblyGearJointError,
        joint_factory=joint_factory,
    )


def verify_gear_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    return verify_rotation_coupling(
        document,
        draft,
        spec_type=GearJointSpec,
        contract=_GEAR_CONTRACT,
        error_type=NativeAssemblyGearJointError,
        **kwargs,
    )
