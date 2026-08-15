# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact canonical Angle-joint contract over the regular-joint engine."""

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


MIN_ANGLE_DEGREES = 0.0
MAX_ANGLE_DEGREES = 180.0
AXIS_DOT_TOLERANCE = 1.0e-6
_FALLBACK_SOLVER_CONFUSION_RADIANS = 1.0e-7


class NativeAssemblyAngleJointError(RuntimeError):
    """An exact Angle-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_ANGLE_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AngleJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    angle_degrees: float
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedAngleJoint = PreparedRegularJoint


def canonical_angle_degrees(
    value: Any,
    field: str = "angle_degrees",
) -> float:
    """Return one finite, non-aliased angle between two coordinate-system axes."""

    if isinstance(value, bool):
        raise NativeAssemblyAngleJointError(
            f"{field} must be an angle in degrees."
        )
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyAngleJointError(
            f"{field} must be an angle in degrees."
        ) from exc
    if not (
        math.isfinite(number)
        and MIN_ANGLE_DEGREES <= number <= MAX_ANGLE_DEGREES
    ):
        raise NativeAssemblyAngleJointError(
            f"{field} must be from 0 through 180 degrees."
        )
    return number


def _regular_spec(spec: AngleJointSpec) -> RegularJointSpec:
    if not isinstance(spec, AngleJointSpec):
        raise TypeError("spec must be an AngleJointSpec")
    angle = canonical_angle_degrees(spec.angle_degrees)
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Angle",
        type_index=8,
        label=spec.label,
        reverse=False,
        properties=(RegularJointPropertySpec("Angle", angle),),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _axis_dot(joint: Any) -> float | None:
    try:
        import FreeCAD as App
        import UtilsAssembly

        first = UtilsAssembly.getJcsGlobalPlc(joint.Placement1, joint.Reference1)
        second = UtilsAssembly.getJcsGlobalPlc(joint.Placement2, joint.Reference2)
        first_z = first.Rotation.multVec(App.Vector(0, 0, 1))
        second_z = second.Rotation.multVec(App.Vector(0, 0, 1))
        return max(-1.0, min(1.0, float(first_z.dot(second_z))))
    except (
        AttributeError,
        ImportError,
        IndexError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return None


def measured_axis_angle_degrees(joint: Any) -> float | None:
    """Measure the principal angle between the two live global connector Z axes."""

    dot = _axis_dot(joint)
    return None if dot is None else math.degrees(math.acos(dot))


def _solver_confusion_radians() -> float:
    try:
        import Part

        return float(Part.Precision.confusion())
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return _FALLBACK_SOLVER_CONFUSION_RADIANS


def angle_solver_relation(expected_degrees: Any) -> str:
    """Name the compiled constraint relation used for one canonical angle."""

    expected = canonical_angle_degrees(expected_degrees)
    angle_radians = math.radians(expected)
    if math.fmod(angle_radians, 2.0 * math.pi) < _solver_confusion_radians():
        return "parallel_unsigned"
    return "axis_dot_cosine"


def angle_axes_satisfied(joint: Any, expected_degrees: Any) -> bool:
    """Match the live axes to the exact compiled Angle-joint semantics."""

    try:
        expected = canonical_angle_degrees(expected_degrees)
    except NativeAssemblyAngleJointError:
        return False
    dot = _axis_dot(joint)
    if dot is None:
        return False
    angle_radians = math.radians(expected)
    if angle_solver_relation(expected) == "parallel_unsigned":
        # AssemblyObject deliberately substitutes ASMTParallelAxesJoint here.
        return abs(abs(dot) - 1.0) <= AXIS_DOT_TOLERANCE
    return abs(dot - math.cos(angle_radians)) <= AXIS_DOT_TOLERANCE


def _angle_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblyAngleJointError:
    return NativeAssemblyAngleJointError(str(exc))


def preflight_angle_joint(
    document: Any,
    spec: AngleJointSpec,
    **kwargs: Any,
) -> PreparedRegularJoint:
    try:
        return preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _angle_failure(exc) from exc


def apply_angle_joint(
    document: Any,
    spec: AngleJointSpec,
    *,
    joint_factory: Callable[[Any, Any, AngleJointSpec], Any] | None = None,
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
        raise _angle_failure(exc) from exc


def verify_angle_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _angle_failure(exc) from exc
    result.pop("reverse")
    stored_angle = float(result.pop("properties")["Angle"])
    measured = measured_axis_angle_degrees(draft.value["joint"])
    satisfied = angle_axes_satisfied(draft.value["joint"], stored_angle)
    if draft.value["spec"].expected_solve_on_creation and not satisfied:
        raise NativeAssemblyAngleJointError(
            "The native Angle joint did not establish its requested connector-axis "
            "angle."
        )
    result["angle_degrees"] = stored_angle
    result["angle_relation"] = angle_solver_relation(stored_angle)
    result["measured_axis_angle_degrees"] = measured
    result["angle_satisfied"] = satisfied
    return result
