# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Cylindrical-joint contract over the shared regular-joint engine."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

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
from VibeCADNativeTargets import NativeObjectRef


MIN_ANGLE_DEGREES = -180.0
MAX_ANGLE_DEGREES = 180.0
MIN_LENGTH_MM = -1_000_000.0
MAX_LENGTH_MM = 1_000_000.0


class NativeAssemblyCylindricalJointError(RuntimeError):
    """An exact Cylindrical-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_CYLINDRICAL_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class CylindricalJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    reverse: bool
    length_minimum_enabled: bool
    length_minimum_mm: float
    length_maximum_enabled: bool
    length_maximum_mm: float
    angle_minimum_enabled: bool
    angle_minimum_degrees: float
    angle_maximum_enabled: bool
    angle_maximum_degrees: float
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedCylindricalJoint = PreparedRegularJoint


def _bounded_value(
    value: Any,
    field: str,
    minimum: float,
    maximum: float,
    unit: str,
) -> float:
    if isinstance(value, bool):
        raise NativeAssemblyCylindricalJointError(f"{field} must be in {unit}.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyCylindricalJointError(
            f"{field} must be in {unit}."
        ) from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise NativeAssemblyCylindricalJointError(
            f"{field} must be from {minimum:g} through {maximum:g} {unit}."
        )
    return number


def cylindrical_length_mm(value: Any, field: str) -> float:
    """Return one finite Cylindrical travel limit in the Native envelope."""

    return _bounded_value(
        value,
        field,
        MIN_LENGTH_MM,
        MAX_LENGTH_MM,
        "mm",
    )


def cylindrical_angle_degrees(value: Any, field: str) -> float:
    """Return one finite Cylindrical angular limit accepted by the human UI."""

    return _bounded_value(
        value,
        field,
        MIN_ANGLE_DEGREES,
        MAX_ANGLE_DEGREES,
        "degrees",
    )


def _regular_spec(spec: CylindricalJointSpec) -> RegularJointSpec:
    if not isinstance(spec, CylindricalJointSpec):
        raise TypeError("spec must be a CylindricalJointSpec")
    enabled = (
        spec.length_minimum_enabled,
        spec.length_maximum_enabled,
        spec.angle_minimum_enabled,
        spec.angle_maximum_enabled,
    )
    if any(type(value) is not bool for value in enabled):
        raise NativeAssemblyCylindricalJointError(
            "Cylindrical limit enabled states must be true or false."
        )
    length_minimum = cylindrical_length_mm(
        spec.length_minimum_mm,
        "length_minimum_mm",
    )
    length_maximum = cylindrical_length_mm(
        spec.length_maximum_mm,
        "length_maximum_mm",
    )
    angle_minimum = cylindrical_angle_degrees(
        spec.angle_minimum_degrees,
        "angle_minimum_degrees",
    )
    angle_maximum = cylindrical_angle_degrees(
        spec.angle_maximum_degrees,
        "angle_maximum_degrees",
    )
    if (
        spec.length_minimum_enabled
        and spec.length_maximum_enabled
        and length_minimum > length_maximum
    ):
        raise NativeAssemblyCylindricalJointError(
            "An enabled Cylindrical minimum length cannot exceed its maximum."
        )
    if (
        spec.angle_minimum_enabled
        and spec.angle_maximum_enabled
        and angle_minimum > angle_maximum
    ):
        raise NativeAssemblyCylindricalJointError(
            "An enabled Cylindrical minimum angle cannot exceed its maximum."
        )
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Cylindrical",
        type_index=2,
        label=spec.label,
        reverse=spec.reverse,
        properties=(
            RegularJointPropertySpec(
                "EnableLengthMin", spec.length_minimum_enabled
            ),
            RegularJointPropertySpec("LengthMin", length_minimum),
            RegularJointPropertySpec(
                "EnableLengthMax", spec.length_maximum_enabled
            ),
            RegularJointPropertySpec("LengthMax", length_maximum),
            RegularJointPropertySpec(
                "EnableAngleMin", spec.angle_minimum_enabled
            ),
            RegularJointPropertySpec("AngleMin", angle_minimum),
            RegularJointPropertySpec(
                "EnableAngleMax", spec.angle_maximum_enabled
            ),
            RegularJointPropertySpec("AngleMax", angle_maximum),
        ),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _failure(exc: NativeAssemblyRegularJointError) -> NativeAssemblyCylindricalJointError:
    return NativeAssemblyCylindricalJointError(str(exc))


def preflight_cylindrical_joint(
    document: Any,
    spec: CylindricalJointSpec,
    **kwargs: Any,
) -> PreparedRegularJoint:
    try:
        return preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _failure(exc) from exc


def apply_cylindrical_joint(
    document: Any,
    spec: CylindricalJointSpec,
    *,
    joint_factory: Callable[[Any, Any, CylindricalJointSpec], Any] | None = None,
) -> NativeMutationDraft:
    regular = _regular_spec(spec)
    kwargs: dict[str, Any] = {}
    if joint_factory is not None:
        kwargs["joint_factory"] = (
            lambda assembly, joint_group, _spec: joint_factory(
                assembly,
                joint_group,
                spec,
            )
        )
    try:
        return apply_regular_joint(document, regular, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _failure(exc) from exc


def verify_cylindrical_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _failure(exc) from exc
    properties = result.pop("properties")
    result["limits"] = {
        "length": {
            "minimum": {
                "enabled": bool(properties["EnableLengthMin"]),
                "mm": float(properties["LengthMin"]),
            },
            "maximum": {
                "enabled": bool(properties["EnableLengthMax"]),
                "mm": float(properties["LengthMax"]),
            },
        },
        "angle": {
            "minimum": {
                "enabled": bool(properties["EnableAngleMin"]),
                "degrees": float(properties["AngleMin"]),
            },
            "maximum": {
                "enabled": bool(properties["EnableAngleMax"]),
                "degrees": float(properties["AngleMax"]),
            },
        },
    }
    return result
