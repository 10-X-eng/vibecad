# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Drag Knife dress-up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureDressupSupport import (
    MAX_DRESSUP_COMMANDS,
    PreparedDressupBase,
    assert_dressup_preflight_current,
    command_path_sha256,
    cutting_command_count,
    dressup_error,
    preflight_dressup_base,
    publish_dressup_replacement,
    verify_dressup_envelope,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import finite_number
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_DRAG_KNIFE_INPUT_COMMANDS = 50_000


@dataclass(frozen=True, slots=True)
class DragKnifeDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    corner_filter_angle_degrees: Any
    blade_offset_mm: Any
    pivot_height_mm: Any


@dataclass(frozen=True, slots=True)
class PreparedDragKnifeDressup:
    base: PreparedDressupBase
    corner_filter_angle_degrees: float
    blade_offset_mm: float
    pivot_height_mm: float
    corner_candidate_count: int
    corner_action_count: int
    corner_action_depths_mm: tuple[float, ...]
    line_extension_count: int
    arc_extension_count: int
    line_twist_count: int
    arc_twist_count: int
    inserted_command_count: int
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str


def _generation_counts(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "corner_candidate_count": int(metadata["corner_candidate_count"]),
        "corner_action_count": int(metadata["corner_action_count"]),
        "corner_action_depths_mm": tuple(
            float(value) for value in metadata["corner_action_depths_mm"]
        ),
        "line_extension_count": int(metadata["line_extension_count"]),
        "arc_extension_count": int(metadata["arc_extension_count"]),
        "line_twist_count": int(metadata["line_twist_count"]),
        "arc_twist_count": int(metadata["arc_twist_count"]),
    }


def preflight_drag_knife_dressup(
    document: Any,
    spec: DragKnifeDressupSpec,
) -> PreparedDragKnifeDressup:
    """Freeze one exact base and prepare its complete blade compensation."""

    if not isinstance(spec, DragKnifeDressupSpec):
        raise TypeError("spec must be a DragKnifeDressupSpec")
    filter_angle = finite_number(
        spec.corner_filter_angle_degrees,
        "CAM Drag Knife corner_filter_angle_degrees",
        minimum=0.0,
        maximum=180.0,
    )
    blade_offset = finite_number(
        spec.blade_offset_mm,
        "CAM Drag Knife blade_offset_mm",
        minimum=0.0,
        maximum=100.0,
    )
    if blade_offset <= 0.0:
        dressup_error("CAM Drag Knife blade_offset_mm must be greater than zero.")
    pivot_height = finite_number(
        spec.pivot_height_mm,
        "CAM Drag Knife pivot_height_mm",
        minimum=0.0,
        maximum=100.0,
    )
    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM Drag Knife dress-up",
    )
    input_commands = tuple(base.base.Path.Commands or ())
    if len(input_commands) > MAX_DRAG_KNIFE_INPUT_COMMANDS:
        dressup_error(
            f"CAM Drag Knife base has {len(input_commands)} commands; its interactive "
            f"limit is {MAX_DRAG_KNIFE_INPUT_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    try:
        import Path.Dressup.Dragknife as Dragknife
        import PathScripts.PathUtils as PathUtils

        expected_path, metadata = Dragknife.generatePathWithMetadata(
            base.base,
            filter_angle_degrees=filter_angle,
            offset_mm=blade_offset,
            pivot_height_mm=pivot_height,
        )
        commands = tuple(expected_path.Commands or ())
        placed_base_count = len(
            tuple(PathUtils.getPathWithPlacement(base.base).Commands or ())
        )
        counts = _generation_counts(metadata)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM Drag Knife toolpath could not be prepared.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    action_depths = counts["corner_action_depths_mm"]
    if action_depths and pivot_height <= max(action_depths):
        dressup_error(
            "CAM Drag Knife pivot_height_mm must be above every compensated cutting "
            "depth.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "highest_compensated_depth_mm": max(action_depths),
                "minimum_exclusive_pivot_height_mm": max(action_depths),
            },
        )
    if len(commands) > MAX_DRESSUP_COMMANDS:
        dressup_error(
            f"CAM Drag Knife would generate {len(commands)} commands; the safety "
            f"limit is {MAX_DRESSUP_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    inserted = len(commands) - placed_base_count
    cutting = cutting_command_count(commands)
    if inserted <= 0:
        dressup_error(
            "CAM Drag Knife found no eligible corner or exit segment to compensate.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "corner_candidate_count": counts["corner_candidate_count"],
                "corner_action_count": counts["corner_action_count"],
                "corner_filter_angle_degrees": filter_angle,
            },
        )
    if cutting <= 0:
        dressup_error(
            "CAM Drag Knife did not retain a usable cutting path.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    return PreparedDragKnifeDressup(
        base=base,
        corner_filter_angle_degrees=filter_angle,
        blade_offset_mm=blade_offset,
        pivot_height_mm=pivot_height,
        corner_candidate_count=counts["corner_candidate_count"],
        corner_action_count=counts["corner_action_count"],
        corner_action_depths_mm=action_depths,
        line_extension_count=counts["line_extension_count"],
        arc_extension_count=counts["arc_extension_count"],
        line_twist_count=counts["line_twist_count"],
        arc_twist_count=counts["arc_twist_count"],
        inserted_command_count=inserted,
        expected_command_count=len(commands),
        expected_cutting_count=cutting,
        expected_path_sha256=command_path_sha256(
            commands,
            "CAM Drag Knife dress-up",
        ),
    )


def create_drag_knife_dressup(
    document: Any,
    *,
    prepared: PreparedDragKnifeDressup,
) -> NativeMutationDraft:
    """Create and configure one Drag Knife replacement in the owned transaction."""

    if not isinstance(prepared, PreparedDragKnifeDressup):
        raise TypeError("prepared must be a PreparedDragKnifeDressup")
    assert_dressup_preflight_current(document, prepared.base)
    base = prepared.base
    try:
        import Path.Dressup.Gui.Dragknife as DragknifeGui

        operation = DragknifeGui.CreateInTransaction(
            base.base,
            hide_base=False,
        )
        operation.Label = base.label
        operation.filterAngle = prepared.corner_filter_angle_degrees
        operation.offset = prepared.blade_offset_mm
        operation.pivotheight = prepared.pivot_height_mm
        publish_dressup_replacement(document, base, operation)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Drag Knife factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "operation": operation},
        recompute_targets=(operation,),
        created=(object_identity(operation),),
        changed=(object_identity(base.job),),
        replaced=(object_identity(base.base),),
    )


def verify_created_drag_knife_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact blade compensation and replacement History state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedDragKnifeDressup) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Drag Knife dress-up")
    base = prepared.base

    import Path.Dressup.Gui.Dragknife as DragknifeGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=DragknifeGui.ObjectDressup,
        view_proxy_type=DragknifeGui.ViewProviderDressup,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
    )
    expected_center = tuple(round(float(value), 9) for value in base.job.Path.Center)
    actual_center = tuple(round(float(value), 9) for value in operation.Path.Center)
    actual_counts = _generation_counts(operation.Proxy.lastGenerationStats)
    expected_counts = {
        "corner_candidate_count": prepared.corner_candidate_count,
        "corner_action_count": prepared.corner_action_count,
        "corner_action_depths_mm": prepared.corner_action_depths_mm,
        "line_extension_count": prepared.line_extension_count,
        "arc_extension_count": prepared.arc_extension_count,
        "line_twist_count": prepared.line_twist_count,
        "arc_twist_count": prepared.arc_twist_count,
    }
    if (
        round(float(operation.filterAngle.Value), 9)
        != prepared.corner_filter_angle_degrees
        or round(float(operation.offset), 9) != prepared.blade_offset_mm
        or round(float(operation.pivotheight), 9) != prepared.pivot_height_mm
        or actual_counts != expected_counts
        or actual_center != expected_center
    ):
        dressup_error(
            "The created CAM Drag Knife did not retain its exact compensation "
            "settings, generation counts, or rotary center.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "drag_knife_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "corner_filter_angle_degrees": prepared.corner_filter_angle_degrees,
        "blade_offset_mm": prepared.blade_offset_mm,
        "pivot_height_mm": prepared.pivot_height_mm,
        "corner_candidate_count": prepared.corner_candidate_count,
        "corner_action_count": prepared.corner_action_count,
        "corner_action_depths_mm": list(prepared.corner_action_depths_mm),
        "line_extension_count": prepared.line_extension_count,
        "arc_extension_count": prepared.arc_extension_count,
        "line_twist_count": prepared.line_twist_count,
        "arc_twist_count": prepared.arc_twist_count,
        "inserted_command_count": prepared.inserted_command_count,
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "path_center_mm": list(actual_center),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
