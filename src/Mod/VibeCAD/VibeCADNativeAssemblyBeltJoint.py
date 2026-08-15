# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Belt coupling over two explicit Revolute prerequisites."""

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


MIN_BELT_RADIUS_MM = MIN_COUPLING_RADIUS_MM
MAX_BELT_RADIUS_MM = MAX_COUPLING_RADIUS_MM


class NativeAssemblyBeltJointError(RuntimeError):
    """An exact Belt request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_BELT_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class BeltJointSpec:
    assembly_ref: NativeObjectRef
    first_pulley_connector: JointConnectorSpec
    second_pulley_connector: JointConnectorSpec
    first_revolute_joint_ref: NativeObjectRef
    second_revolute_joint_ref: NativeObjectRef
    label: str
    radius1_mm: float
    radius2_mm: float
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedBeltJoint = PreparedRotationCoupling


_BELT_CONTRACT = RotationCouplingContract(
    joint_type="Belt",
    type_index=12,
    coupling_label="Belt",
    connector_label="Pulley",
    component_noun="pulley",
    first_connector_result_key="first_pulley_connector",
    second_connector_result_key="second_pulley_connector",
    rotation_multiplier=1.0,
    rotation_direction="same",
)


def belt_radius_mm(value: Any, field: str) -> float:
    return positive_coupling_radius_mm(value, field, NativeAssemblyBeltJointError)


def _regular_spec(spec: BeltJointSpec) -> RegularJointSpec:
    if not isinstance(spec, BeltJointSpec):
        raise TypeError("spec must be a BeltJointSpec")
    return regular_rotation_coupling_spec(
        spec,
        spec.first_pulley_connector,
        spec.second_pulley_connector,
        _BELT_CONTRACT,
        NativeAssemblyBeltJointError,
    )


def _validate_dependencies(
    prepared: PreparedRegularJoint,
    spec: BeltJointSpec,
    first_revolute: Any,
    second_revolute: Any,
) -> tuple[int, int]:
    return validate_rotation_coupling_dependencies(
        prepared,
        spec.first_pulley_connector,
        spec.second_pulley_connector,
        first_revolute,
        second_revolute,
        _BELT_CONTRACT,
        NativeAssemblyBeltJointError,
    )


def belt_dependency_summary(
    joint: Any,
    active_joints: Iterable[Any],
) -> dict[str, Any] | None:
    return rotation_coupling_dependency_summary(joint, active_joints)


def preflight_belt_joint(
    document: Any,
    spec: BeltJointSpec,
    **kwargs: Any,
) -> PreparedBeltJoint:
    if not isinstance(spec, BeltJointSpec):
        raise TypeError("spec must be a BeltJointSpec")
    return preflight_rotation_coupling(
        document,
        spec,
        first_connector=spec.first_pulley_connector,
        second_connector=spec.second_pulley_connector,
        first_revolute_joint_ref=spec.first_revolute_joint_ref,
        second_revolute_joint_ref=spec.second_revolute_joint_ref,
        first_dependency_label="first pulley Revolute joint",
        second_dependency_label="second pulley Revolute joint",
        contract=_BELT_CONTRACT,
        error_type=NativeAssemblyBeltJointError,
        **kwargs,
    )


def apply_belt_joint(
    document: Any,
    spec: BeltJointSpec,
    *,
    joint_factory: Callable[[Any, Any, BeltJointSpec], Any] | None = None,
) -> NativeMutationDraft:
    return apply_rotation_coupling(
        document,
        spec,
        first_connector=spec.first_pulley_connector,
        second_connector=spec.second_pulley_connector,
        first_revolute_joint_ref=spec.first_revolute_joint_ref,
        second_revolute_joint_ref=spec.second_revolute_joint_ref,
        first_dependency_label="first pulley Revolute joint",
        second_dependency_label="second pulley Revolute joint",
        contract=_BELT_CONTRACT,
        error_type=NativeAssemblyBeltJointError,
        joint_factory=joint_factory,
    )


def verify_belt_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    return verify_rotation_coupling(
        document,
        draft,
        spec_type=BeltJointSpec,
        contract=_BELT_CONTRACT,
        error_type=NativeAssemblyBeltJointError,
        **kwargs,
    )
