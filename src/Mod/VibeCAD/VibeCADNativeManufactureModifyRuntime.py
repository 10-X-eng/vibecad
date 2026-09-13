# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for deterministic CAM operation modifications."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError
from VibeCADNativeManufactureCopy import (
    copy_operations,
    preflight_operation_copy,
    prepare_operation_copy_spec,
    verify_operation_copy,
)
from VibeCADNativeManufactureDressupArray import (
    ArrayDressupSpec,
    create_array_dressup,
    preflight_array_dressup,
    verify_created_array_dressup,
)
from VibeCADNativeManufactureDressupAxisMap import (
    AxisMapDressupSpec,
    create_axis_map_dressup,
    preflight_axis_map_dressup,
    verify_created_axis_map_dressup,
)
from VibeCADNativeManufactureDressupDogbone import (
    DogboneDressupSpec,
    create_dogbone_dressup,
    preflight_dogbone_dressup,
    verify_created_dogbone_dressup,
)
from VibeCADNativeManufactureDressupDragKnife import (
    DragKnifeDressupSpec,
    create_drag_knife_dressup,
    preflight_drag_knife_dressup,
    verify_created_drag_knife_dressup,
)
from VibeCADNativeManufactureDressupLeadInOut import (
    LeadInOutDressupSpec,
    create_lead_in_out_dressup,
    preflight_lead_in_out_dressup,
    verify_created_lead_in_out_dressup,
)
from VibeCADNativeManufactureDressupMirror import (
    MirrorDressupSpec,
    create_mirror_dressup,
    preflight_mirror_dressup,
    verify_created_mirror_dressup,
)
from VibeCADNativeManufactureDressupPathBoundary import (
    PathBoundaryDressupSpec,
    create_path_boundary_dressup,
    preflight_path_boundary_dressup,
    verify_created_path_boundary_dressup,
)
from VibeCADNativeManufactureDressupRampEntry import (
    RampEntryDressupSpec,
    create_ramp_entry_dressup,
    preflight_ramp_entry_dressup,
    verify_created_ramp_entry_dressup,
)
from VibeCADNativeManufactureDressupTag import (
    TagDressupSpec,
    create_tag_dressup,
    preflight_tag_dressup,
    verify_created_tag_dressup,
)
from VibeCADNativeManufactureDressupZCorrect import (
    ZCorrectDressupSpec,
    create_z_correct_dressup,
    preflight_z_correct_boundary,
    prepare_z_correct_dressup,
    verify_created_z_correct_dressup,
    z_correct_input_request,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureModify import (
    preflight_operation_active,
    prepare_operation_active_spec,
    set_operation_active,
    verify_operation_active,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_VARIANTS = {
    "set_active": frozenset({"job", "targets"}),
    "copy_operations": frozenset({"jobs"}),
    "array_dressup": frozenset(
        {"label", "job", "base_operation", "pattern", "jitter"}
    ),
    "axis_map_dressup": frozenset(
        {"label", "job", "base_operation", "axis_mapping", "radius_mm", "reverse"}
    ),
    "dogbone_dressup": frozenset(
        {
            "label",
            "job",
            "base_operation",
            "style",
            "side",
            "incision",
            "only_closed_profiles",
            "disabled_bone_locations_mm",
        }
    ),
    "drag_knife_dressup": frozenset(
        {
            "label",
            "job",
            "base_operation",
            "corner_filter_angle_degrees",
            "blade_offset_mm",
            "pivot_height_mm",
        }
    ),
    "lead_in_out_dressup": frozenset(
        {
            "label",
            "job",
            "base_operation",
            "lead_in",
            "lead_out",
            "retract_threshold_mm",
            "rapid_plunge",
        }
    ),
    "mirror_dressup": frozenset(
        {"label", "job", "base_operation", "mirror"}
    ),
    "path_boundary_dressup": frozenset(
        {
            "label",
            "job",
            "base_operation",
            "boundary",
            "inside",
            "offset_mm",
            "retract_threshold_mm",
            "rest_machining_pass",
        }
    ),
    "ramp_entry_dressup": frozenset(
        {
            "label",
            "job",
            "base_operation",
            "method",
            "angle_from_vertical_degrees",
            "activation",
        }
    ),
    "tag_dressup": frozenset(
        {"label", "job", "base_operation", "placement"}
    ),
    "z_correct_dressup": frozenset(
        {
            "label",
            "job",
            "base_operation",
            "arc_maximum_deflection_mm",
            "line_maximum_segment_length_mm",
        }
    ),
}


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "resource_scope": str(snapshot.resource_scope),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


class NativeManufactureModifyRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def modify(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        current = context.state.current_revision(context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)
        if operation == "set_active":
            spec = prepare_operation_active_spec(values["job"], values["targets"])
            prepared = preflight_operation_active(context.document, spec)
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Set Native CAM Operation State",
                mutate=partial(set_operation_active, prepared=prepared),
                verify=verify_operation_active,
            )
        if operation == "copy_operations":
            spec = prepare_operation_copy_spec(values["jobs"])
            prepared = preflight_operation_copy(context.document, spec)
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Copy Native CAM Operations",
                mutate=partial(copy_operations, prepared=prepared),
                verify=verify_operation_copy,
            )
        if operation == "array_dressup":
            prepared = preflight_array_dressup(
                context.document,
                ArrayDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    pattern=values["pattern"],
                    jitter=values["jitter"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Array Dress-up",
                mutate=partial(create_array_dressup, prepared=prepared),
                verify=verify_created_array_dressup,
            )
        if operation == "axis_map_dressup":
            prepared = preflight_axis_map_dressup(
                context.document,
                AxisMapDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    axis_mapping=values["axis_mapping"],
                    radius_mm=values["radius_mm"],
                    reverse=values["reverse"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Axis Map Dress-up",
                mutate=partial(create_axis_map_dressup, prepared=prepared),
                verify=verify_created_axis_map_dressup,
            )
        if operation == "dogbone_dressup":
            prepared = preflight_dogbone_dressup(
                context.document,
                DogboneDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    style=values["style"],
                    side=values["side"],
                    incision=values["incision"],
                    only_closed_profiles=values["only_closed_profiles"],
                    disabled_bone_locations_mm=values[
                        "disabled_bone_locations_mm"
                    ],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Dogbone Dress-up",
                mutate=partial(create_dogbone_dressup, prepared=prepared),
                verify=verify_created_dogbone_dressup,
            )
        if operation == "drag_knife_dressup":
            prepared = preflight_drag_knife_dressup(
                context.document,
                DragKnifeDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    corner_filter_angle_degrees=values[
                        "corner_filter_angle_degrees"
                    ],
                    blade_offset_mm=values["blade_offset_mm"],
                    pivot_height_mm=values["pivot_height_mm"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Drag Knife Dress-up",
                mutate=partial(create_drag_knife_dressup, prepared=prepared),
                verify=verify_created_drag_knife_dressup,
            )
        if operation == "lead_in_out_dressup":
            prepared = preflight_lead_in_out_dressup(
                context.document,
                LeadInOutDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    lead_in=values["lead_in"],
                    lead_out=values["lead_out"],
                    retract_threshold_mm=values["retract_threshold_mm"],
                    rapid_plunge=values["rapid_plunge"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Lead In/Out Dress-up",
                mutate=partial(create_lead_in_out_dressup, prepared=prepared),
                verify=verify_created_lead_in_out_dressup,
            )
        if operation == "mirror_dressup":
            prepared = preflight_mirror_dressup(
                context.document,
                MirrorDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    mirror=values["mirror"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Mirror Dress-up",
                mutate=partial(create_mirror_dressup, prepared=prepared),
                verify=verify_created_mirror_dressup,
            )
        if operation == "path_boundary_dressup":
            prepared = preflight_path_boundary_dressup(
                context.document,
                PathBoundaryDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    boundary=values["boundary"],
                    inside=values["inside"],
                    offset_mm=values["offset_mm"],
                    retract_threshold_mm=values["retract_threshold_mm"],
                    rest_machining_pass=values["rest_machining_pass"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Path Boundary Dress-up",
                mutate=partial(create_path_boundary_dressup, prepared=prepared),
                verify=verify_created_path_boundary_dressup,
            )
        if operation == "ramp_entry_dressup":
            prepared = preflight_ramp_entry_dressup(
                context.document,
                RampEntryDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    method=values["method"],
                    angle_from_vertical_degrees=values[
                        "angle_from_vertical_degrees"
                    ],
                    activation=values["activation"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Ramp Entry Dress-up",
                mutate=partial(create_ramp_entry_dressup, prepared=prepared),
                verify=verify_created_ramp_entry_dressup,
            )
        if operation == "tag_dressup":
            prepared = preflight_tag_dressup(
                context.document,
                TagDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    placement=values["placement"],
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Holding-Tag Dress-up",
                mutate=partial(create_tag_dressup, prepared=prepared),
                verify=verify_created_tag_dressup,
            )
        if operation == "z_correct_dressup":
            boundary = preflight_z_correct_boundary(
                context.document,
                ZCorrectDressupSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operation=values["base_operation"],
                    arc_maximum_deflection_mm=values[
                        "arc_maximum_deflection_mm"
                    ],
                    line_maximum_segment_length_mm=values[
                        "line_maximum_segment_length_mm"
                    ],
                ),
            )
            authorizer = context.authorize_input
            manager = context.background_manager
            dispatcher = context.document_thread_dispatch
            if authorizer is None or manager is None or dispatcher is None:
                raise NativeManufactureError(
                    "Human probe-map authorization and background preparation are "
                    "unavailable in this session.",
                    error_code="NATIVE_MANUFACTURE_Z_CORRECT_UNAVAILABLE",
                )
            request = z_correct_input_request()
            try:
                authorization = authorizer(request)
            except NativeInputError as exc:
                raise NativeManufactureError(str(exc), error_code=exc.code) from exc
            if authorization is None:
                raise NativeManufactureError(
                    "The human cancelled probe-map selection.",
                    error_code="NATIVE_MANUFACTURE_Z_CORRECT_CANCELLED",
                )

            def prepare(cancelled: Any, progress: Any) -> Any:
                return prepare_z_correct_dressup(
                    boundary,
                    authorization,
                    request,
                    cancelled=cancelled,
                    progress=progress,
                )

            def commit(prepared: Any) -> Mapping[str, Any]:
                return run_immediate_mutation(
                    context,
                    ticket=ticket,
                    transaction_name="Create Native CAM Z Correction Dress-up",
                    mutate=partial(
                        create_z_correct_dressup,
                        prepared=prepared,
                    ),
                    verify=verify_created_z_correct_dressup,
                )

            try:
                snapshot = manager.submit(
                    document_uid=context.document_uid,
                    capability_name="manufacture.modify.z_correct_dressup",
                    prepare=prepare,
                    validate_before_commit=context.guard,
                    commit=commit,
                    dispatch_to_document_thread=dispatcher,
                    finalize_message="Committing CAM Z Correction",
                    resource_scope=f"manufacture:{boundary.base.job.Name}",
                )
            except NativeBackgroundError as exc:
                raise NativeManufactureError(
                    str(exc),
                    error_code="NATIVE_MANUFACTURE_Z_CORRECT_QUEUE_FAILED",
                ) from exc
            return {
                "job": _job_summary(snapshot),
                "next": {
                    "tool": "native.job",
                    "operation": "status",
                    "job_id": snapshot.job_id,
                },
            }
        raise RuntimeError("The requested CAM modification is unavailable.")
