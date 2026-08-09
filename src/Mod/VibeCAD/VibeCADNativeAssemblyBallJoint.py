# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Ball-joint contract over the shared regular-joint engine."""

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


class NativeAssemblyBallJointError(RuntimeError):
    """An exact Ball-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_BALL_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class BallJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedBallJoint = PreparedRegularJoint


def _regular_spec(spec: BallJointSpec) -> RegularJointSpec:
    if not isinstance(spec, BallJointSpec):
        raise TypeError("spec must be a BallJointSpec")
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Ball",
        type_index=4,
        label=spec.label,
        reverse=False,
        properties=(),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _ball_failure(exc: NativeAssemblyRegularJointError) -> NativeAssemblyBallJointError:
    return NativeAssemblyBallJointError(str(exc))


def preflight_ball_joint(
    document: Any,
    spec: BallJointSpec,
    **kwargs: Any,
) -> PreparedRegularJoint:
    try:
        return preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _ball_failure(exc) from exc


def apply_ball_joint(
    document: Any,
    spec: BallJointSpec,
    *,
    joint_factory: Callable[[Any, Any, BallJointSpec], Any] | None = None,
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
        raise _ball_failure(exc) from exc


def verify_ball_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _ball_failure(exc) from exc
    result.pop("reverse")
    result.pop("properties")
    return result
