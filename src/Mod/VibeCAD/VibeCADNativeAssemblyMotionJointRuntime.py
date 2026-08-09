# SPDX-License-Identifier: LGPL-2.1-or-later

"""Execution routing for exact Native Assembly motion joints."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyBallJoint import (
    BallJointSpec,
    NativeAssemblyBallJointError,
    apply_ball_joint,
    preflight_ball_joint,
    verify_ball_joint,
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
from VibeCADNativeAssemblyJointArguments import (
    joint_bool,
    joint_connector,
    joint_count,
    joint_label,
    joint_limit_pair,
    joint_object_ref,
)
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
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


MOTION_JOINT_OPERATIONS = frozenset(
    {
        "create_fixed",
        "create_revolute",
        "create_cylindrical",
        "create_slider",
        "create_ball",
    }
)


def _revolute_limits(value: Any) -> tuple[bool, float, bool, float]:
    return joint_limit_pair(
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
    length = joint_limit_pair(
        value["length"],
        "limits.length",
        "mm",
        cylindrical_length_mm,
        NativeAssemblyCylindricalJointError,
    )
    angle = joint_limit_pair(
        value["angle"],
        "limits.angle",
        "degrees",
        cylindrical_angle_degrees,
        NativeAssemblyCylindricalJointError,
    )
    return (*length, *angle)


def _slider_limits(value: Any) -> tuple[bool, float, bool, float]:
    return joint_limit_pair(
        value,
        "limits",
        "mm",
        slider_length_mm,
        NativeAssemblySliderJointError,
    )


def execute_motion_joint(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
    operation: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Decode and execute one already-authorized motion-joint variant."""

    document_uid = context.document_uid
    if operation == "create_ball":
        spec = BallJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyBallJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblyBallJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblyBallJointError,
            ),
            label=joint_label(values["label"], NativeAssemblyBallJointError),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyBallJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyBallJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyBallJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyBallJointError,
            ),
        )
        context.guard()
        preflight_ball_joint(context.document, spec)
        return run_immediate_mutation(
            context,
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
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblySliderJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblySliderJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblySliderJointError,
            ),
            label=joint_label(values["label"], NativeAssemblySliderJointError),
            reverse=joint_bool(
                values["reverse"],
                "reverse",
                NativeAssemblySliderJointError,
            ),
            minimum_enabled=minimum_enabled,
            minimum_mm=minimum,
            maximum_enabled=maximum_enabled,
            maximum_mm=maximum,
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblySliderJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblySliderJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblySliderJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblySliderJointError,
            ),
        )
        context.guard()
        preflight_slider_joint(context.document, spec)
        return run_immediate_mutation(
            context,
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
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyCylindricalJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblyCylindricalJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblyCylindricalJointError,
            ),
            label=joint_label(
                values["label"],
                NativeAssemblyCylindricalJointError,
            ),
            reverse=joint_bool(
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
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyCylindricalJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyCylindricalJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyCylindricalJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyCylindricalJointError,
            ),
        )
        context.guard()
        preflight_cylindrical_joint(context.document, spec)
        return run_immediate_mutation(
            context,
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
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyRevoluteJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblyRevoluteJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblyRevoluteJointError,
            ),
            label=joint_label(values["label"], NativeAssemblyRevoluteJointError),
            reverse=joint_bool(
                values["reverse"],
                "reverse",
                NativeAssemblyRevoluteJointError,
            ),
            minimum_enabled=minimum_enabled,
            minimum_degrees=minimum,
            maximum_enabled=maximum_enabled,
            maximum_degrees=maximum,
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyRevoluteJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyRevoluteJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyRevoluteJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyRevoluteJointError,
            ),
        )
        context.guard()
        preflight_revolute_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Revolute Joint",
            mutate=lambda document: apply_revolute_joint(document, spec),
            verify=verify_revolute_joint,
        )
    if operation == "create_fixed":
        spec = FixedJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyFixedJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblyFixedJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblyFixedJointError,
            ),
            label=joint_label(values["label"], NativeAssemblyFixedJointError),
            reverse=joint_bool(
                values["reverse"],
                "reverse",
                NativeAssemblyFixedJointError,
            ),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyFixedJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyFixedJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyFixedJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyFixedJointError,
            ),
        )
        context.guard()
        preflight_fixed_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Fixed Joint",
            mutate=lambda document: apply_fixed_joint(document, spec),
            verify=verify_fixed_joint,
        )
    raise RuntimeError("Unsupported Native Assembly motion-joint operation.")
