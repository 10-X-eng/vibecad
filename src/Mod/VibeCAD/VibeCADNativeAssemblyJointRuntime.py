# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly joint operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyGrounding import (
    GroundingSpec,
    GroundingTargetSpec,
    MAX_GROUNDING_TARGETS,
    NativeAssemblyGroundingError,
    apply_grounding,
    preflight_grounding,
    verify_grounding,
)
from VibeCADNativeAssemblyMotionJointRuntime import (
    MOTION_JOINT_OPERATIONS,
    execute_motion_joint,
)
from VibeCADNativeAssemblyRelationJointRuntime import (
    RELATION_JOINT_OPERATIONS,
    execute_relation_joint,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


_COMMON_JOINT_FIELDS = frozenset(
    {
        "assembly",
        "first",
        "second",
        "label",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    }
)
_OPERATION_FIELDS = {
    "set_grounded": frozenset(
        {
            "assembly",
            "targets",
            "grounded",
            "expected_component_count",
            "expected_grounded_count",
        }
    ),
    "create_fixed": _COMMON_JOINT_FIELDS | {"reverse"},
    "create_revolute": _COMMON_JOINT_FIELDS | {"reverse", "limits"},
    "create_cylindrical": _COMMON_JOINT_FIELDS | {"reverse", "limits"},
    "create_slider": _COMMON_JOINT_FIELDS | {"reverse", "limits"},
    "create_ball": _COMMON_JOINT_FIELDS,
    "create_distance": _COMMON_JOINT_FIELDS
    | {"reverse", "distance_mm", "expected_distance_mode"},
    "create_parallel": _COMMON_JOINT_FIELDS | {"reverse"},
    "create_perpendicular": _COMMON_JOINT_FIELDS,
    "create_angle": _COMMON_JOINT_FIELDS | {"angle_degrees"},
    "create_rack_pinion": {
        "assembly",
        "rack_connector",
        "pinion_connector",
        "rack_slider_joint",
        "pinion_revolute_joint",
        "label",
        "pitch_radius_mm",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
        "expected_solve_on_creation",
    },
}


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000:
        raise NativeAssemblyGroundingError(
            f"{field} must be an integer from 0 through 100000."
        )
    return value


def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise NativeAssemblyGroundingError(f"{field} must be true or false.")
    return value


def _object_ref(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyGroundingError(
            f"{field} must be one exact current-document object reference."
        )
    return NativeObjectRef(document_uid, str(value.get("object_name") or ""))


def _targets(document_uid: str, value: Any) -> tuple[GroundingTargetSpec, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= MAX_GROUNDING_TARGETS
    ):
        raise NativeAssemblyGroundingError(
            f"targets must contain 1 to {MAX_GROUNDING_TARGETS} exact components."
        )
    result = []
    names: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "component",
            "expected_grounded",
        }:
            raise NativeAssemblyGroundingError(
                f"targets[{index}] must contain component and expected_grounded."
            )
        component = _object_ref(
            document_uid,
            item["component"],
            f"targets[{index}].component",
        )
        if component.object_name in names:
            raise NativeAssemblyGroundingError(
                "targets cannot repeat an exact component."
            )
        names.add(component.object_name)
        result.append(
            GroundingTargetSpec(
                component_ref=component,
                expected_grounded=_bool(
                    item["expected_grounded"],
                    f"targets[{index}].expected_grounded",
                ),
            )
        )
    return tuple(result)


class NativeAssemblyJointRuntime:
    """Execute only joint operations authorized for one frozen Assemble turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_joint(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _OPERATION_FIELDS)
        if operation in MOTION_JOINT_OPERATIONS:
            return execute_motion_joint(
                self._context,
                ticket,
                operation,
                values,
            )
        if operation in RELATION_JOINT_OPERATIONS:
            return execute_relation_joint(
                self._context,
                ticket,
                operation,
                values,
            )
        spec = GroundingSpec(
            assembly_ref=_object_ref(
                self._context.document_uid,
                values["assembly"],
                "assembly",
            ),
            targets=_targets(self._context.document_uid, values["targets"]),
            grounded=_bool(values["grounded"], "grounded"),
            expected_component_count=_count(
                values["expected_component_count"],
                "expected_component_count",
            ),
            expected_grounded_count=_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
            ),
        )
        self._context.guard()
        preflight_grounding(self._context.document, spec)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=(
                "Ground Native Assembly Components"
                if spec.grounded
                else "Unground Native Assembly Components"
            ),
            mutate=lambda document: apply_grounding(document, spec),
            verify=verify_grounding,
        )
