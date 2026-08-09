# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly joint operations."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyBallJoint import (
    BallJointSpec,
    NativeAssemblyBallJointError,
    apply_ball_joint,
    preflight_ball_joint,
    verify_ball_joint,
)
from VibeCADNativeAssemblyGrounding import (
    GroundingSpec,
    GroundingTargetSpec,
    MAX_GROUNDING_TARGETS,
    NativeAssemblyGroundingError,
    apply_grounding,
    preflight_grounding,
    verify_grounding,
)
from VibeCADNativeAssemblyCylindricalJoint import (
    CylindricalJointSpec,
    NativeAssemblyCylindricalJointError,
    apply_cylindrical_joint,
    cylindrical_angle_degrees,
    cylindrical_length_mm,
    preflight_cylindrical_joint,
    verify_cylindrical_joint,
)
from VibeCADNativeAssemblyFixedJoint import (
    FixedJointSpec,
    NativeAssemblyFixedJointError,
    apply_fixed_joint,
    preflight_fixed_joint,
    verify_fixed_joint,
)
from VibeCADNativeAssemblyJointConnectors import JointConnectorSpec
from VibeCADNativeAssemblyRevoluteJoint import (
    NativeAssemblyRevoluteJointError,
    RevoluteJointSpec,
    apply_revolute_joint,
    preflight_revolute_joint,
    revolute_limit_degrees,
    verify_revolute_joint,
)
from VibeCADNativeAssemblySliderJoint import (
    NativeAssemblySliderJointError,
    SliderJointSpec,
    apply_slider_joint,
    preflight_slider_joint,
    slider_length_mm,
    verify_slider_joint,
)
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


def _joint_count(
    value: Any,
    field: str,
    error_type: type[RuntimeError],
    maximum: int = 100_000,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        raise error_type(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _joint_bool(
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> bool:
    if type(value) is not bool:
        raise error_type(f"{field} must be true or false.")
    return value


def _joint_object_ref(
    document_uid: str,
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise error_type(
            f"{field} must be one exact current-document object reference."
        )
    return NativeObjectRef(document_uid, str(value.get("object_name") or ""))


def _placement(
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> Any:
    try:
        return part_placement_from_mapping(value)
    except (NativeModelError, KeyError, TypeError, ValueError) as exc:
        raise error_type(
            f"{field} must contain a finite origin and non-zero axis rotation."
        ) from exc


def _connector(
    document_uid: str,
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> JointConnectorSpec:
    required = {
        "component",
        "element_path",
        "anchor_path",
        "offset",
        "expected_component_placement",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise error_type(
            f"{field} must contain one exact component-rooted connector."
        )
    element_path = value["element_path"]
    anchor_path = value["anchor_path"]
    if not isinstance(element_path, str) or not isinstance(anchor_path, str):
        raise error_type(
            f"{field} connector paths must be strings."
        )
    return JointConnectorSpec(
        component_ref=_joint_object_ref(
            document_uid,
            value["component"],
            f"{field}.component",
            error_type,
        ),
        element_path=element_path,
        anchor_path=anchor_path,
        offset=_placement(value["offset"], f"{field}.offset", error_type),
        expected_component_placement=_placement(
            value["expected_component_placement"],
            f"{field}.expected_component_placement",
            error_type,
        ),
    )


def _label(value: Any, error_type: type[RuntimeError]) -> str:
    if not isinstance(value, str):
        raise error_type("label must be text.")
    label = value.strip()
    if not label or len(label) > 160:
        raise error_type(
            "label must contain 1 to 160 non-whitespace characters."
        )
    return label


def _joint_limit(
    value: Any,
    field: str,
    value_key: str,
    converter: Callable[[Any, str], float],
    error_type: type[RuntimeError],
) -> tuple[bool, float]:
    if not isinstance(value, Mapping) or set(value) != {"enabled", value_key}:
        raise error_type(f"{field} must contain enabled and {value_key}.")
    return (
        _joint_bool(
            value["enabled"],
            f"{field}.enabled",
            error_type,
        ),
        converter(value[value_key], f"{field}.{value_key}"),
    )


def _joint_limit_pair(
    value: Any,
    field: str,
    value_key: str,
    converter: Callable[[Any, str], float],
    error_type: type[RuntimeError],
) -> tuple[bool, float, bool, float]:
    if not isinstance(value, Mapping) or set(value) != {"minimum", "maximum"}:
        raise error_type(
            f"{field} must contain exact minimum and maximum states."
        )
    minimum_enabled, minimum = _joint_limit(
        value["minimum"],
        f"{field}.minimum",
        value_key,
        converter,
        error_type,
    )
    maximum_enabled, maximum = _joint_limit(
        value["maximum"],
        f"{field}.maximum",
        value_key,
        converter,
        error_type,
    )
    return minimum_enabled, minimum, maximum_enabled, maximum


def _revolute_limits(value: Any) -> tuple[bool, float, bool, float]:
    return _joint_limit_pair(
        value,
        "limits",
        "degrees",
        revolute_limit_degrees,
        NativeAssemblyRevoluteJointError,
    )


def _cylindrical_limits(
    value: Any,
) -> tuple[bool, float, bool, float, bool, float, bool, float]:
    if not isinstance(value, Mapping) or set(value) != {"length", "angle"}:
        raise NativeAssemblyCylindricalJointError(
            "limits must contain exact length and angle states."
        )
    length = _joint_limit_pair(
        value["length"],
        "limits.length",
        "mm",
        cylindrical_length_mm,
        NativeAssemblyCylindricalJointError,
    )
    angle = _joint_limit_pair(
        value["angle"],
        "limits.angle",
        "degrees",
        cylindrical_angle_degrees,
        NativeAssemblyCylindricalJointError,
    )
    return (*length, *angle)


def _slider_limits(value: Any) -> tuple[bool, float, bool, float]:
    return _joint_limit_pair(
        value,
        "limits",
        "mm",
        slider_length_mm,
        NativeAssemblySliderJointError,
    )


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
                "create_revolute": frozenset(
                    {
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "limits",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    }
                ),
                "create_cylindrical": frozenset(
                    {
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "limits",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    }
                ),
                "create_slider": frozenset(
                    {
                        "assembly",
                        "first",
                        "second",
                        "label",
                        "reverse",
                        "limits",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                        "expected_solve_on_creation",
                    }
                ),
                "create_ball": frozenset(
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
                ),
            },
        )
        if operation == "create_ball":
            spec = BallJointSpec(
                assembly_ref=_joint_object_ref(
                    self._context.document_uid,
                    values["assembly"],
                    "assembly",
                    NativeAssemblyBallJointError,
                ),
                first=_connector(
                    self._context.document_uid,
                    values["first"],
                    "first",
                    NativeAssemblyBallJointError,
                ),
                second=_connector(
                    self._context.document_uid,
                    values["second"],
                    "second",
                    NativeAssemblyBallJointError,
                ),
                label=_label(values["label"], NativeAssemblyBallJointError),
                expected_component_count=_joint_count(
                    values["expected_component_count"],
                    "expected_component_count",
                    NativeAssemblyBallJointError,
                ),
                expected_grounded_count=_joint_count(
                    values["expected_grounded_count"],
                    "expected_grounded_count",
                    NativeAssemblyBallJointError,
                ),
                expected_joint_count=_joint_count(
                    values["expected_joint_count"],
                    "expected_joint_count",
                    NativeAssemblyBallJointError,
                    256,
                ),
                expected_solve_on_creation=_joint_bool(
                    values["expected_solve_on_creation"],
                    "expected_solve_on_creation",
                    NativeAssemblyBallJointError,
                ),
            )
            self._context.guard()
            preflight_ball_joint(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Ball Joint",
                mutate=lambda document: apply_ball_joint(document, spec),
                verify=verify_ball_joint,
            )
        if operation == "create_slider":
            minimum_enabled, minimum, maximum_enabled, maximum = _slider_limits(
                values["limits"]
            )
            spec = SliderJointSpec(
                assembly_ref=_joint_object_ref(
                    self._context.document_uid,
                    values["assembly"],
                    "assembly",
                    NativeAssemblySliderJointError,
                ),
                first=_connector(
                    self._context.document_uid,
                    values["first"],
                    "first",
                    NativeAssemblySliderJointError,
                ),
                second=_connector(
                    self._context.document_uid,
                    values["second"],
                    "second",
                    NativeAssemblySliderJointError,
                ),
                label=_label(values["label"], NativeAssemblySliderJointError),
                reverse=_joint_bool(
                    values["reverse"],
                    "reverse",
                    NativeAssemblySliderJointError,
                ),
                minimum_enabled=minimum_enabled,
                minimum_mm=minimum,
                maximum_enabled=maximum_enabled,
                maximum_mm=maximum,
                expected_component_count=_joint_count(
                    values["expected_component_count"],
                    "expected_component_count",
                    NativeAssemblySliderJointError,
                ),
                expected_grounded_count=_joint_count(
                    values["expected_grounded_count"],
                    "expected_grounded_count",
                    NativeAssemblySliderJointError,
                ),
                expected_joint_count=_joint_count(
                    values["expected_joint_count"],
                    "expected_joint_count",
                    NativeAssemblySliderJointError,
                    256,
                ),
                expected_solve_on_creation=_joint_bool(
                    values["expected_solve_on_creation"],
                    "expected_solve_on_creation",
                    NativeAssemblySliderJointError,
                ),
            )
            self._context.guard()
            preflight_slider_joint(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Slider Joint",
                mutate=lambda document: apply_slider_joint(document, spec),
                verify=verify_slider_joint,
            )
        if operation == "create_cylindrical":
            (
                length_minimum_enabled,
                length_minimum,
                length_maximum_enabled,
                length_maximum,
                angle_minimum_enabled,
                angle_minimum,
                angle_maximum_enabled,
                angle_maximum,
            ) = _cylindrical_limits(values["limits"])
            spec = CylindricalJointSpec(
                assembly_ref=_joint_object_ref(
                    self._context.document_uid,
                    values["assembly"],
                    "assembly",
                    NativeAssemblyCylindricalJointError,
                ),
                first=_connector(
                    self._context.document_uid,
                    values["first"],
                    "first",
                    NativeAssemblyCylindricalJointError,
                ),
                second=_connector(
                    self._context.document_uid,
                    values["second"],
                    "second",
                    NativeAssemblyCylindricalJointError,
                ),
                label=_label(
                    values["label"],
                    NativeAssemblyCylindricalJointError,
                ),
                reverse=_joint_bool(
                    values["reverse"],
                    "reverse",
                    NativeAssemblyCylindricalJointError,
                ),
                length_minimum_enabled=length_minimum_enabled,
                length_minimum_mm=length_minimum,
                length_maximum_enabled=length_maximum_enabled,
                length_maximum_mm=length_maximum,
                angle_minimum_enabled=angle_minimum_enabled,
                angle_minimum_degrees=angle_minimum,
                angle_maximum_enabled=angle_maximum_enabled,
                angle_maximum_degrees=angle_maximum,
                expected_component_count=_joint_count(
                    values["expected_component_count"],
                    "expected_component_count",
                    NativeAssemblyCylindricalJointError,
                ),
                expected_grounded_count=_joint_count(
                    values["expected_grounded_count"],
                    "expected_grounded_count",
                    NativeAssemblyCylindricalJointError,
                ),
                expected_joint_count=_joint_count(
                    values["expected_joint_count"],
                    "expected_joint_count",
                    NativeAssemblyCylindricalJointError,
                    256,
                ),
                expected_solve_on_creation=_joint_bool(
                    values["expected_solve_on_creation"],
                    "expected_solve_on_creation",
                    NativeAssemblyCylindricalJointError,
                ),
            )
            self._context.guard()
            preflight_cylindrical_joint(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Cylindrical Joint",
                mutate=lambda document: apply_cylindrical_joint(document, spec),
                verify=verify_cylindrical_joint,
            )
        if operation == "create_revolute":
            minimum_enabled, minimum, maximum_enabled, maximum = _revolute_limits(
                values["limits"]
            )
            spec = RevoluteJointSpec(
                assembly_ref=_joint_object_ref(
                    self._context.document_uid,
                    values["assembly"],
                    "assembly",
                    NativeAssemblyRevoluteJointError,
                ),
                first=_connector(
                    self._context.document_uid,
                    values["first"],
                    "first",
                    NativeAssemblyRevoluteJointError,
                ),
                second=_connector(
                    self._context.document_uid,
                    values["second"],
                    "second",
                    NativeAssemblyRevoluteJointError,
                ),
                label=_label(values["label"], NativeAssemblyRevoluteJointError),
                reverse=_joint_bool(
                    values["reverse"],
                    "reverse",
                    NativeAssemblyRevoluteJointError,
                ),
                minimum_enabled=minimum_enabled,
                minimum_degrees=minimum,
                maximum_enabled=maximum_enabled,
                maximum_degrees=maximum,
                expected_component_count=_joint_count(
                    values["expected_component_count"],
                    "expected_component_count",
                    NativeAssemblyRevoluteJointError,
                ),
                expected_grounded_count=_joint_count(
                    values["expected_grounded_count"],
                    "expected_grounded_count",
                    NativeAssemblyRevoluteJointError,
                ),
                expected_joint_count=_joint_count(
                    values["expected_joint_count"],
                    "expected_joint_count",
                    NativeAssemblyRevoluteJointError,
                    256,
                ),
                expected_solve_on_creation=_joint_bool(
                    values["expected_solve_on_creation"],
                    "expected_solve_on_creation",
                    NativeAssemblyRevoluteJointError,
                ),
            )
            self._context.guard()
            preflight_revolute_joint(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Revolute Joint",
                mutate=lambda document: apply_revolute_joint(document, spec),
                verify=verify_revolute_joint,
            )
        if operation == "create_fixed":
            spec = FixedJointSpec(
                assembly_ref=_joint_object_ref(
                    self._context.document_uid,
                    values["assembly"],
                    "assembly",
                    NativeAssemblyFixedJointError,
                ),
                first=_connector(
                    self._context.document_uid,
                    values["first"],
                    "first",
                    NativeAssemblyFixedJointError,
                ),
                second=_connector(
                    self._context.document_uid,
                    values["second"],
                    "second",
                    NativeAssemblyFixedJointError,
                ),
                label=_label(values["label"], NativeAssemblyFixedJointError),
                reverse=_joint_bool(
                    values["reverse"],
                    "reverse",
                    NativeAssemblyFixedJointError,
                ),
                expected_component_count=_joint_count(
                    values["expected_component_count"],
                    "expected_component_count",
                    NativeAssemblyFixedJointError,
                ),
                expected_grounded_count=_joint_count(
                    values["expected_grounded_count"],
                    "expected_grounded_count",
                    NativeAssemblyFixedJointError,
                ),
                expected_joint_count=_joint_count(
                    values["expected_joint_count"],
                    "expected_joint_count",
                    NativeAssemblyFixedJointError,
                    256,
                ),
                expected_solve_on_creation=_joint_bool(
                    values["expected_solve_on_creation"],
                    "expected_solve_on_creation",
                    NativeAssemblyFixedJointError,
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
