# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Fixed-joint contract over the shared regular-joint engine."""

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


class NativeAssemblyFixedJointError(RuntimeError):
    """An exact Fixed-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_FIXED_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class FixedJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    reverse: bool
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedFixedJoint = PreparedRegularJoint


def _regular_spec(spec: FixedJointSpec) -> RegularJointSpec:
    if not isinstance(spec, FixedJointSpec):
        raise TypeError("spec must be a FixedJointSpec")
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Fixed",
        type_index=0,
        label=spec.label,
        reverse=spec.reverse,
        properties=(),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _fixed_failure(exc: NativeAssemblyRegularJointError) -> NativeAssemblyFixedJointError:
    return NativeAssemblyFixedJointError(str(exc))


def preflight_fixed_joint(
    document: Any,
    spec: FixedJointSpec,
    **kwargs: Any,
) -> PreparedRegularJoint:
    try:
        return preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _fixed_failure(exc) from exc


def apply_fixed_joint(
    document: Any,
    spec: FixedJointSpec,
    *,
    joint_factory: Callable[[Any, Any, str], Any] | None = None,
) -> NativeMutationDraft:
    regular = _regular_spec(spec)
    kwargs: dict[str, Any] = {}
    if joint_factory is not None:
        kwargs["joint_factory"] = (
            lambda assembly, joint_group, _spec: joint_factory(
                assembly,
                joint_group,
                spec.label,
            )
        )
    try:
        return apply_regular_joint(document, regular, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _fixed_failure(exc) from exc


def verify_fixed_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _fixed_failure(exc) from exc
