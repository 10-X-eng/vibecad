# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Parallel-joint contract over the shared regular-joint engine."""

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


class NativeAssemblyParallelJointError(RuntimeError):
    """An exact Parallel-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_PARALLEL_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class ParallelJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    reverse: bool
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


PreparedParallelJoint = PreparedRegularJoint


def _regular_spec(spec: ParallelJointSpec) -> RegularJointSpec:
    if not isinstance(spec, ParallelJointSpec):
        raise TypeError("spec must be a ParallelJointSpec")
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Parallel",
        type_index=6,
        label=spec.label,
        reverse=spec.reverse,
        properties=(),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def parallel_axes_satisfied(joint: Any) -> bool:
    """Return whether the two live joint-connector Z axes are parallel."""

    try:
        import UtilsAssembly

        first = UtilsAssembly.getJcsGlobalPlc(joint.Placement1, joint.Reference1)
        second = UtilsAssembly.getJcsGlobalPlc(joint.Placement2, joint.Reference2)
        return bool(UtilsAssembly.arePlacementZParallel(first, second))
    except (AttributeError, IndexError, ReferenceError, RuntimeError, TypeError):
        return False


def _parallel_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblyParallelJointError:
    return NativeAssemblyParallelJointError(str(exc))


def preflight_parallel_joint(
    document: Any,
    spec: ParallelJointSpec,
    **kwargs: Any,
) -> PreparedRegularJoint:
    try:
        return preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _parallel_failure(exc) from exc


def apply_parallel_joint(
    document: Any,
    spec: ParallelJointSpec,
    *,
    joint_factory: Callable[[Any, Any, ParallelJointSpec], Any] | None = None,
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
        raise _parallel_failure(exc) from exc


def verify_parallel_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _parallel_failure(exc) from exc
    result.pop("properties")
    satisfied = parallel_axes_satisfied(draft.value["joint"])
    if draft.value["spec"].expected_solve_on_creation and not satisfied:
        raise NativeAssemblyParallelJointError(
            "The native Parallel joint did not make its connector Z axes parallel."
        )
    result["axes_parallel"] = satisfied
    return result
