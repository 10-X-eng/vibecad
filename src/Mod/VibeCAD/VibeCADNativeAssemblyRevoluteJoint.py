# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Revolute-joint contract over the shared regular-joint engine."""

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


MIN_REVOLUTE_LIMIT_DEGREES = -180.0
MAX_REVOLUTE_LIMIT_DEGREES = 180.0


class NativeAssemblyRevoluteJointError(RuntimeError):
    """An exact Revolute-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_REVOLUTE_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class RevoluteJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    reverse: bool
    minimum_enabled: bool
    minimum_degrees: float
    maximum_enabled: bool
    maximum_degrees: float
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedRevoluteJoint = PreparedRegularJoint


def revolute_limit_degrees(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise NativeAssemblyRevoluteJointError(f"{field} must be an angle in degrees.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyRevoluteJointError(
            f"{field} must be an angle in degrees."
        ) from exc
    if not (
        math.isfinite(number)
        and MIN_REVOLUTE_LIMIT_DEGREES <= number <= MAX_REVOLUTE_LIMIT_DEGREES
    ):
        raise NativeAssemblyRevoluteJointError(
            f"{field} must be from -180 through 180 degrees."
        )
    return number


def _regular_spec(spec: RevoluteJointSpec) -> RegularJointSpec:
    if not isinstance(spec, RevoluteJointSpec):
        raise TypeError("spec must be a RevoluteJointSpec")
    if type(spec.minimum_enabled) is not bool or type(spec.maximum_enabled) is not bool:
        raise NativeAssemblyRevoluteJointError(
            "Revolute limit enabled states must be true or false."
        )
    minimum = revolute_limit_degrees(spec.minimum_degrees, "minimum_degrees")
    maximum = revolute_limit_degrees(spec.maximum_degrees, "maximum_degrees")
    if spec.minimum_enabled and spec.maximum_enabled and minimum > maximum:
        raise NativeAssemblyRevoluteJointError(
            "An enabled Revolute minimum angle cannot exceed its maximum angle."
        )
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Revolute",
        type_index=1,
        label=spec.label,
        reverse=spec.reverse,
        properties=(
            RegularJointPropertySpec("EnableAngleMin", spec.minimum_enabled),
            RegularJointPropertySpec("AngleMin", minimum),
            RegularJointPropertySpec("EnableAngleMax", spec.maximum_enabled),
            RegularJointPropertySpec("AngleMax", maximum),
        ),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _revolute_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblyRevoluteJointError:
    return NativeAssemblyRevoluteJointError(str(exc))


def preflight_revolute_joint(
    document: Any,
    spec: RevoluteJointSpec,
    **kwargs: Any,
) -> PreparedRegularJoint:
    try:
        return preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _revolute_failure(exc) from exc


def apply_revolute_joint(
    document: Any,
    spec: RevoluteJointSpec,
    *,
    joint_factory: Callable[[Any, Any, RevoluteJointSpec], Any] | None = None,
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
        raise _revolute_failure(exc) from exc


def verify_revolute_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _revolute_failure(exc) from exc
    result["limits"] = {
        "minimum": {
            "enabled": bool(result["properties"]["EnableAngleMin"]),
            "degrees": float(result["properties"]["AngleMin"]),
        },
        "maximum": {
            "enabled": bool(result["properties"]["EnableAngleMax"]),
            "degrees": float(result["properties"]["AngleMax"]),
        },
    }
    result.pop("properties", None)
    return result
