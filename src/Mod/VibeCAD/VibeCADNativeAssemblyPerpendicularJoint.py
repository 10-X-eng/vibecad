# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Perpendicular-joint contract over the shared regular-joint engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyJointConnectors import JointConnectorSpec
from VibeCADNativeAssemblyRegularJoint import (
    NativeAssemblyRegularJointError,
    PreparedRegularJoint,
    RegularJointSpec,
    apply_regular_joint,
    preflight_regular_joint,
    verify_regular_joint,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef


AXIS_DOT_TOLERANCE = 1.0e-6


class NativeAssemblyPerpendicularJointError(RuntimeError):
    """An exact Perpendicular-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_PERPENDICULAR_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class PerpendicularJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedPerpendicularJoint = PreparedRegularJoint


def _regular_spec(spec: PerpendicularJointSpec) -> RegularJointSpec:
    if not isinstance(spec, PerpendicularJointSpec):
        raise TypeError("spec must be a PerpendicularJointSpec")
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Perpendicular",
        type_index=7,
        label=spec.label,
        reverse=False,
        properties=(),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def perpendicular_axes_satisfied(joint: Any) -> bool:
    """Return whether the two live joint-connector Z axes are perpendicular."""

    try:
        import FreeCAD as App
        import UtilsAssembly

        first = UtilsAssembly.getJcsGlobalPlc(joint.Placement1, joint.Reference1)
        second = UtilsAssembly.getJcsGlobalPlc(joint.Placement2, joint.Reference2)
        first_z = first.Rotation.multVec(App.Vector(0, 0, 1))
        second_z = second.Rotation.multVec(App.Vector(0, 0, 1))
        return abs(float(first_z.dot(second_z))) < AXIS_DOT_TOLERANCE
    except (
        AttributeError,
        ImportError,
        IndexError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return False


def _perpendicular_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblyPerpendicularJointError:
    return NativeAssemblyPerpendicularJointError(str(exc))


def preflight_perpendicular_joint(
    document: Any,
    spec: PerpendicularJointSpec,
    **kwargs: Any,
) -> PreparedRegularJoint:
    try:
        return preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _perpendicular_failure(exc) from exc


def apply_perpendicular_joint(
    document: Any,
    spec: PerpendicularJointSpec,
    *,
    joint_factory: Callable[[Any, Any, PerpendicularJointSpec], Any] | None = None,
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
        raise _perpendicular_failure(exc) from exc


def verify_perpendicular_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _perpendicular_failure(exc) from exc
    result.pop("reverse")
    result.pop("properties")
    satisfied = perpendicular_axes_satisfied(draft.value["joint"])
    if draft.value["spec"].expected_solve_on_creation and not satisfied:
        raise NativeAssemblyPerpendicularJointError(
            "The native Perpendicular joint did not make its connector Z axes "
            "perpendicular."
        )
    result["axes_perpendicular"] = satisfied
    return result
