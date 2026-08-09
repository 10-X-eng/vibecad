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
from VibeCADNativeAssemblyFixedJoint import (
    FixedJointSpec,
    NativeAssemblyFixedJointError,
    apply_fixed_joint,
    preflight_fixed_joint,
    verify_fixed_joint,
)
from VibeCADNativeAssemblyJointConnectors import JointConnectorSpec
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativePartPrimitives import part_placement_from_mapping
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


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


def _fixed_count(value: Any, field: str, maximum: int = 100_000) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        raise NativeAssemblyFixedJointError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _fixed_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise NativeAssemblyFixedJointError(f"{field} must be true or false.")
    return value


def _fixed_object_ref(
    document_uid: str,
    value: Any,
    field: str,
) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyFixedJointError(
            f"{field} must be one exact current-document object reference."
        )
    return NativeObjectRef(document_uid, str(value.get("object_name") or ""))


def _placement(value: Any, field: str) -> Any:
    try:
        return part_placement_from_mapping(value)
    except (NativeModelError, KeyError, TypeError, ValueError) as exc:
        raise NativeAssemblyFixedJointError(
            f"{field} must contain a finite origin and non-zero axis rotation."
        ) from exc


def _connector(document_uid: str, value: Any, field: str) -> JointConnectorSpec:
    required = {
        "component",
        "element_path",
        "anchor_path",
        "offset",
        "expected_component_placement",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAssemblyFixedJointError(
            f"{field} must contain one exact component-rooted connector."
        )
    element_path = value["element_path"]
    anchor_path = value["anchor_path"]
    if not isinstance(element_path, str) or not isinstance(anchor_path, str):
        raise NativeAssemblyFixedJointError(
            f"{field} connector paths must be strings."
        )
    return JointConnectorSpec(
        component_ref=_fixed_object_ref(
            document_uid,
            value["component"],
            f"{field}.component",
        ),
        element_path=element_path,
        anchor_path=anchor_path,
        offset=_placement(value["offset"], f"{field}.offset"),
        expected_component_placement=_placement(
            value["expected_component_placement"],
            f"{field}.expected_component_placement",
        ),
    )


def _label(value: Any) -> str:
    if not isinstance(value, str):
        raise NativeAssemblyFixedJointError("label must be text.")
    label = value.strip()
    if not label or len(label) > 160:
        raise NativeAssemblyFixedJointError(
            "label must contain 1 to 160 non-whitespace characters."
        )
    return label


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
        operation, values = strict_variant_arguments(
            arguments,
            {
                "set_grounded": frozenset(
                    {
                        "assembly",
                        "targets",
                        "grounded",
                        "expected_component_count",
                        "expected_grounded_count",
                    }
                ),
                "create_fixed": frozenset(
                    {
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    }
                ),
            },
        )
        if operation == "create_fixed":
            spec = FixedJointSpec(
                assembly_ref=_fixed_object_ref(
                    self._context.document_uid,
                    values["assembly"],
                    "assembly",
                ),
                first=_connector(
                    self._context.document_uid,
                    values["first"],
                    "first",
                ),
                second=_connector(
                    self._context.document_uid,
                    values["second"],
                    "second",
                ),
                label=_label(values["label"]),
                reverse=_fixed_bool(values["reverse"], "reverse"),
                expected_component_count=_fixed_count(
                    values["expected_component_count"],
                    "expected_component_count",
                ),
                expected_grounded_count=_fixed_count(
                    values["expected_grounded_count"],
                    "expected_grounded_count",
                ),
                expected_joint_count=_fixed_count(
                    values["expected_joint_count"],
                    "expected_joint_count",
                    256,
                ),
                expected_solve_on_creation=_fixed_bool(
                    values["expected_solve_on_creation"],
                    "expected_solve_on_creation",
                ),
            )
            self._context.guard()
            preflight_fixed_joint(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Fixed Joint",
                mutate=lambda document: apply_fixed_joint(document, spec),
                verify=verify_fixed_joint,
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
