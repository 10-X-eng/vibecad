# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Slider-joint contract over the shared regular-joint engine."""

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


MIN_SLIDER_LENGTH_MM = -1_000_000.0
MAX_SLIDER_LENGTH_MM = 1_000_000.0


class NativeAssemblySliderJointError(RuntimeError):
    """An exact Slider-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_SLIDER_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class SliderJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    reverse: bool
    minimum_enabled: bool
    minimum_mm: float
    maximum_enabled: bool
    maximum_mm: float
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedSliderJoint = PreparedRegularJoint


def slider_length_mm(value: Any, field: str) -> float:
    """Return one finite Slider travel limit in the Native envelope."""

    if isinstance(value, bool):
        raise NativeAssemblySliderJointError(f"{field} must be a length in mm.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblySliderJointError(
            f"{field} must be a length in mm."
        ) from exc
    if not (
        math.isfinite(number)
        and MIN_SLIDER_LENGTH_MM <= number <= MAX_SLIDER_LENGTH_MM
    ):
        raise NativeAssemblySliderJointError(
            f"{field} must be from {MIN_SLIDER_LENGTH_MM:g} through "
            f"{MAX_SLIDER_LENGTH_MM:g} mm."
        )
    return number


def _regular_spec(spec: SliderJointSpec) -> RegularJointSpec:
    if not isinstance(spec, SliderJointSpec):
        raise TypeError("spec must be a SliderJointSpec")
    if type(spec.minimum_enabled) is not bool or type(spec.maximum_enabled) is not bool:
        raise NativeAssemblySliderJointError(
            "Slider limit enabled states must be true or false."
        )
    minimum = slider_length_mm(spec.minimum_mm, "minimum_mm")
    maximum = slider_length_mm(spec.maximum_mm, "maximum_mm")
    if spec.minimum_enabled and spec.maximum_enabled and minimum > maximum:
        raise NativeAssemblySliderJointError(
            "An enabled Slider minimum length cannot exceed its maximum length."
        )
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Slider",
        type_index=3,
        label=spec.label,
        reverse=spec.reverse,
        properties=(
            RegularJointPropertySpec("EnableLengthMin", spec.minimum_enabled),
            RegularJointPropertySpec("LengthMin", minimum),
            RegularJointPropertySpec("EnableLengthMax", spec.maximum_enabled),
            RegularJointPropertySpec("LengthMax", maximum),
        ),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _slider_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblySliderJointError:
    return NativeAssemblySliderJointError(str(exc))


def preflight_slider_joint(
    document: Any,
    spec: SliderJointSpec,
    **kwargs: Any,
) -> PreparedRegularJoint:
    try:
        return preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _slider_failure(exc) from exc


def apply_slider_joint(
    document: Any,
    spec: SliderJointSpec,
    *,
    joint_factory: Callable[[Any, Any, SliderJointSpec], Any] | None = None,
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
        raise _slider_failure(exc) from exc


def verify_slider_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _slider_failure(exc) from exc
    properties = result.pop("properties")
    result["limits"] = {
        "minimum": {
            "enabled": bool(properties["EnableLengthMin"]),
            "mm": float(properties["LengthMin"]),
        },
        "maximum": {
            "enabled": bool(properties["EnableLengthMax"]),
            "mm": float(properties["LengthMax"]),
        },
    }
    return result
