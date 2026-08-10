# SPDX-License-Identifier: LGPL-2.1-or-later

"""Execution routing for exact Native Assembly relation joints."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyAngleJoint import (
    AngleJointSpec,
    NativeAssemblyAngleJointError,
    apply_angle_joint,
    canonical_angle_degrees,
    preflight_angle_joint,
    verify_angle_joint,
)
from VibeCADNativeAssemblyBeltJoint import (
    BeltJointSpec,
    NativeAssemblyBeltJointError,
    apply_belt_joint,
    belt_radius_mm,
    preflight_belt_joint,
    verify_belt_joint,
)
from VibeCADNativeAssemblyDistanceJoint import (
    DistanceJointSpec,
    NativeAssemblyDistanceJointError,
    apply_distance_joint,
    distance_mm,
    preflight_distance_joint,
    verify_distance_joint,
)
from VibeCADNativeAssemblyGearJoint import (
    GearJointSpec,
    NativeAssemblyGearJointError,
    apply_gear_joint,
    gear_radius_mm,
    preflight_gear_joint,
    verify_gear_joint,
)
from VibeCADNativeAssemblyJointArguments import (
    joint_bool,
    joint_connector,
    joint_count,
    joint_label,
    joint_object_ref,
)
from VibeCADNativeAssemblyParallelJoint import (
    NativeAssemblyParallelJointError,
    ParallelJointSpec,
    apply_parallel_joint,
    preflight_parallel_joint,
    verify_parallel_joint,
)
from VibeCADNativeAssemblyPerpendicularJoint import (
    NativeAssemblyPerpendicularJointError,
    PerpendicularJointSpec,
    apply_perpendicular_joint,
    preflight_perpendicular_joint,
    verify_perpendicular_joint,
)
from VibeCADNativeAssemblyRackPinionJoint import (
    NativeAssemblyRackPinionJointError,
    RackPinionJointSpec,
    apply_rack_pinion_joint,
    pitch_radius_mm,
    preflight_rack_pinion_joint,
    verify_rack_pinion_joint,
)
from VibeCADNativeAssemblyScrewJoint import (
    NativeAssemblyScrewJointError,
    ScrewJointSpec,
    apply_screw_joint,
    preflight_screw_joint,
    thread_pitch_mm,
    verify_screw_joint,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


RELATION_JOINT_OPERATIONS = frozenset(
    {
        "create_angle",
        "create_belt",
        "create_distance",
        "create_gears",
        "create_parallel",
        "create_perpendicular",
        "create_rack_pinion",
        "create_screw",
    }
)


def execute_relation_joint(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
    operation: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Decode and execute one already-authorized relation-joint variant."""

    document_uid = context.document_uid
    if operation == "create_belt":
        spec = BeltJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyBeltJointError,
            ),
            first_pulley_connector=joint_connector(
                document_uid,
                values["first_pulley_connector"],
                "first_pulley_connector",
                NativeAssemblyBeltJointError,
            ),
            second_pulley_connector=joint_connector(
                document_uid,
                values["second_pulley_connector"],
                "second_pulley_connector",
                NativeAssemblyBeltJointError,
            ),
            first_revolute_joint_ref=joint_object_ref(
                document_uid,
                values["first_revolute_joint"],
                "first_revolute_joint",
                NativeAssemblyBeltJointError,
            ),
            second_revolute_joint_ref=joint_object_ref(
                document_uid,
                values["second_revolute_joint"],
                "second_revolute_joint",
                NativeAssemblyBeltJointError,
            ),
            label=joint_label(values["label"], NativeAssemblyBeltJointError),
            radius1_mm=belt_radius_mm(values["radius1_mm"], "radius1_mm"),
            radius2_mm=belt_radius_mm(values["radius2_mm"], "radius2_mm"),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyBeltJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyBeltJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyBeltJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyBeltJointError,
            ),
        )
        context.guard()
        preflight_belt_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Belt Joint",
            mutate=lambda document: apply_belt_joint(document, spec),
            verify=verify_belt_joint,
        )
    if operation == "create_gears":
        spec = GearJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyGearJointError,
            ),
            first_gear_connector=joint_connector(
                document_uid,
                values["first_gear_connector"],
                "first_gear_connector",
                NativeAssemblyGearJointError,
            ),
            second_gear_connector=joint_connector(
                document_uid,
                values["second_gear_connector"],
                "second_gear_connector",
                NativeAssemblyGearJointError,
            ),
            first_revolute_joint_ref=joint_object_ref(
                document_uid,
                values["first_revolute_joint"],
                "first_revolute_joint",
                NativeAssemblyGearJointError,
            ),
            second_revolute_joint_ref=joint_object_ref(
                document_uid,
                values["second_revolute_joint"],
                "second_revolute_joint",
                NativeAssemblyGearJointError,
            ),
            label=joint_label(values["label"], NativeAssemblyGearJointError),
            radius1_mm=gear_radius_mm(values["radius1_mm"], "radius1_mm"),
            radius2_mm=gear_radius_mm(values["radius2_mm"], "radius2_mm"),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyGearJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyGearJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyGearJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyGearJointError,
            ),
        )
        context.guard()
        preflight_gear_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Gears Joint",
            mutate=lambda document: apply_gear_joint(document, spec),
            verify=verify_gear_joint,
        )
    if operation == "create_screw":
        spec = ScrewJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyScrewJointError,
            ),
            slider_connector=joint_connector(
                document_uid,
                values["slider_connector"],
                "slider_connector",
                NativeAssemblyScrewJointError,
            ),
            screw_connector=joint_connector(
                document_uid,
                values["screw_connector"],
                "screw_connector",
                NativeAssemblyScrewJointError,
            ),
            slider_joint_ref=joint_object_ref(
                document_uid,
                values["slider_joint"],
                "slider_joint",
                NativeAssemblyScrewJointError,
            ),
            screw_revolute_joint_ref=joint_object_ref(
                document_uid,
                values["screw_revolute_joint"],
                "screw_revolute_joint",
                NativeAssemblyScrewJointError,
            ),
            label=joint_label(
                values["label"],
                NativeAssemblyScrewJointError,
            ),
            thread_pitch_mm=thread_pitch_mm(values["thread_pitch_mm"]),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyScrewJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyScrewJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyScrewJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyScrewJointError,
            ),
        )
        context.guard()
        preflight_screw_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Screw Joint",
            mutate=lambda document: apply_screw_joint(document, spec),
            verify=verify_screw_joint,
        )
    if operation == "create_rack_pinion":
        spec = RackPinionJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyRackPinionJointError,
            ),
            rack_connector=joint_connector(
                document_uid,
                values["rack_connector"],
                "rack_connector",
                NativeAssemblyRackPinionJointError,
            ),
            pinion_connector=joint_connector(
                document_uid,
                values["pinion_connector"],
                "pinion_connector",
                NativeAssemblyRackPinionJointError,
            ),
            rack_slider_joint_ref=joint_object_ref(
                document_uid,
                values["rack_slider_joint"],
                "rack_slider_joint",
                NativeAssemblyRackPinionJointError,
            ),
            pinion_revolute_joint_ref=joint_object_ref(
                document_uid,
                values["pinion_revolute_joint"],
                "pinion_revolute_joint",
                NativeAssemblyRackPinionJointError,
            ),
            label=joint_label(
                values["label"],
                NativeAssemblyRackPinionJointError,
            ),
            pitch_radius_mm=pitch_radius_mm(values["pitch_radius_mm"]),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyRackPinionJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyRackPinionJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyRackPinionJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyRackPinionJointError,
            ),
        )
        context.guard()
        preflight_rack_pinion_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Rack-and-Pinion Joint",
            mutate=lambda document: apply_rack_pinion_joint(document, spec),
            verify=verify_rack_pinion_joint,
        )
    if operation == "create_angle":
        spec = AngleJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyAngleJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblyAngleJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblyAngleJointError,
            ),
            label=joint_label(values["label"], NativeAssemblyAngleJointError),
            angle_degrees=canonical_angle_degrees(values["angle_degrees"]),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyAngleJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyAngleJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyAngleJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyAngleJointError,
            ),
        )
        context.guard()
        preflight_angle_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Angle Joint",
            mutate=lambda document: apply_angle_joint(document, spec),
            verify=verify_angle_joint,
        )
    if operation == "create_perpendicular":
        spec = PerpendicularJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyPerpendicularJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblyPerpendicularJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblyPerpendicularJointError,
            ),
            label=joint_label(
                values["label"],
                NativeAssemblyPerpendicularJointError,
            ),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyPerpendicularJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyPerpendicularJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyPerpendicularJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyPerpendicularJointError,
            ),
        )
        context.guard()
        preflight_perpendicular_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Perpendicular Joint",
            mutate=lambda document: apply_perpendicular_joint(document, spec),
            verify=verify_perpendicular_joint,
        )
    if operation == "create_parallel":
        spec = ParallelJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyParallelJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblyParallelJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblyParallelJointError,
            ),
            label=joint_label(values["label"], NativeAssemblyParallelJointError),
            reverse=joint_bool(
                values["reverse"],
                "reverse",
                NativeAssemblyParallelJointError,
            ),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyParallelJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyParallelJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyParallelJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyParallelJointError,
            ),
        )
        context.guard()
        preflight_parallel_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Parallel Joint",
            mutate=lambda document: apply_parallel_joint(document, spec),
            verify=verify_parallel_joint,
        )
    if operation == "create_distance":
        spec = DistanceJointSpec(
            assembly_ref=joint_object_ref(
                document_uid,
                values["assembly"],
                "assembly",
                NativeAssemblyDistanceJointError,
            ),
            first=joint_connector(
                document_uid,
                values["first"],
                "first",
                NativeAssemblyDistanceJointError,
            ),
            second=joint_connector(
                document_uid,
                values["second"],
                "second",
                NativeAssemblyDistanceJointError,
            ),
            label=joint_label(values["label"], NativeAssemblyDistanceJointError),
            reverse=joint_bool(
                values["reverse"],
                "reverse",
                NativeAssemblyDistanceJointError,
            ),
            distance_mm=distance_mm(values["distance_mm"]),
            expected_distance_mode=str(values["expected_distance_mode"] or ""),
            expected_component_count=joint_count(
                values["expected_component_count"],
                "expected_component_count",
                NativeAssemblyDistanceJointError,
            ),
            expected_grounded_count=joint_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
                NativeAssemblyDistanceJointError,
            ),
            expected_joint_count=joint_count(
                values["expected_joint_count"],
                "expected_joint_count",
                NativeAssemblyDistanceJointError,
                256,
            ),
            expected_solve_on_creation=joint_bool(
                values["expected_solve_on_creation"],
                "expected_solve_on_creation",
                NativeAssemblyDistanceJointError,
            ),
        )
        context.guard()
        preflight_distance_joint(context.document, spec)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Create Native Assembly Distance Joint",
            mutate=lambda document: apply_distance_joint(document, spec),
            verify=verify_distance_joint,
        )
    raise RuntimeError("Unsupported Native Assembly relation-joint operation.")
