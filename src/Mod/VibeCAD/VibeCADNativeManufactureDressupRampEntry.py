# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Ramp Entry dress-up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureDressupSupport import (
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
from VibeCADNativeManufactureOperationSupport import exact_fields, finite_number
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_ACTIVATION_FIELDS = {
    "all_plunges": frozenset({"kind"}),
    "below_absolute_z": frozenset({"kind", "z_mm"}),
}
_METHODS = {
    "forward_then_return": "RampMethod1",
    "reverse_into_cut": "RampMethod2",
    "zigzag": "RampMethod3",
    "contour_helix": "Helix",
}


@dataclass(frozen=True, slots=True)
class RampEntryDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    method: Any
    angle_from_vertical_degrees: Any
    activation: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedRampEntryDressup:
    base: PreparedDressupBase
    public_method: str
    internal_method: str
    angle_from_vertical_degrees: float
    start_depth_mm: float | None
    normalized_command_count: int
    ramped_plunge_count: int
    unchanged_plunge_count: int
    ramp_motion_count: int
    start_depth_split_count: int
    duplicate_command_count_removed: int
    combined_plunge_count: int
    scan_units: int
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str


def _normalize_activation(value: Any) -> float | None:
    if not isinstance(value, Mapping):
        dressup_error("CAM Ramp Entry activation must be one closed request.")
    kind = str(value.get("kind") or "")
    fields = _ACTIVATION_FIELDS.get(kind)
    if fields is None:
        dressup_error(
            "CAM Ramp Entry activation kind must be all_plunges or below_absolute_z."
        )
    item = exact_fields(value, fields, f"CAM Ramp Entry {kind} activation")
    if kind == "all_plunges":
        return None
    depth = finite_number(item["z_mm"], "CAM Ramp Entry activation Z")
    if abs(depth) > 1_000_000.0:
        dressup_error("CAM Ramp Entry activation Z must be within ±1,000,000 mm.")
    return depth


def _metadata_counts(metadata: Mapping[str, Any]) -> dict[str, int]:
    return {
        name: int(metadata[name])
        for name in (
            "normalized_command_count",
            "ramped_plunge_count",
            "unchanged_plunge_count",
            "ramp_motion_count",
            "start_depth_split_count",
            "duplicate_command_count_removed",
            "combined_plunge_count",
            "scan_units",
        )
    }


def preflight_ramp_entry_dressup(
    document: Any,
    spec: RampEntryDressupSpec,
) -> PreparedRampEntryDressup:
    """Freeze one exact base and prepare its complete detached entry path."""

    if not isinstance(spec, RampEntryDressupSpec):
        raise TypeError("spec must be a RampEntryDressupSpec")
    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM Ramp Entry dress-up",
    )
    public_method = str(spec.method or "")
    internal_method = _METHODS.get(public_method)
    if internal_method is None:
        dressup_error(
            "CAM Ramp Entry method must be forward_then_return, reverse_into_cut, "
            "zigzag, or contour_helix."
        )
    angle = finite_number(
        spec.angle_from_vertical_degrees,
        "CAM Ramp Entry angle",
    )
    if not 0.1 <= angle <= 89.9:
        dressup_error(
            "CAM Ramp Entry angle must be between 0.1 and 89.9 degrees from vertical."
        )
    start_depth = _normalize_activation(spec.activation)
    try:
        import Path.Dressup.RampEntry as RampEntry

        feed_rates = RampEntry.feed_rates_from_base(base.base)
        expected_path, metadata = RampEntry.generatePathWithMetadata(
            base.base,
            RampEntry.RampDefinition(
                method=internal_method,
                angle_from_vertical_degrees=angle,
                start_depth_mm=start_depth,
            ),
            feed_rates,
        )
        commands = tuple(expected_path.Commands or ())
        counts = _metadata_counts(metadata)
    except NativeManufactureError:
        raise
    except Exception as exc:
        code = (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
            if "limit" in str(exc).lower() or "exceed" in str(exc).lower()
            else "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
        )
        raise NativeManufactureError(
            "The exact CAM Ramp Entry toolpath could not be prepared.",
            error_code=code,
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    if counts["ramped_plunge_count"] <= 0 or counts["ramp_motion_count"] <= 0:
        dressup_error(
            "CAM Ramp Entry found no eligible descending plunge and following path "
            "for the requested method and activation depth.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            repair={
                "method": public_method,
                "unchanged_plunge_count": counts["unchanged_plunge_count"],
                "start_depth_mm": start_depth,
            },
        )
    required_rates = {
        "horizontal_feed": feed_rates.horizontal_feed,
        "vertical_feed": feed_rates.vertical_feed,
        "ramp_feed": feed_rates.ramp_feed,
        "horizontal_rapid": feed_rates.horizontal_rapid,
        "vertical_rapid": feed_rates.vertical_rapid,
    }
    unavailable_rates = sorted(
        name for name, value in required_rates.items() if float(value) <= 0.0
    )
    if unavailable_rates:
        dressup_error(
            "CAM Ramp Entry requires positive feed and rapid rates on the inherited "
            "tool controller.",
            "NATIVE_MANUFACTURE_MACHINE_PARAMETERS_UNAVAILABLE",
            repair={
                "tool_controller_name": str(base.controller.Name),
                "required_positive_properties": unavailable_rates,
            },
        )
    cutting = cutting_command_count(commands)
    if cutting <= 0:
        dressup_error(
            "CAM Ramp Entry did not retain a usable cutting path.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    return PreparedRampEntryDressup(
        base=base,
        public_method=public_method,
        internal_method=internal_method,
        angle_from_vertical_degrees=angle,
        start_depth_mm=start_depth,
        **counts,
        expected_command_count=len(commands),
        expected_cutting_count=cutting,
        expected_path_sha256=command_path_sha256(
            commands,
            "CAM Ramp Entry dress-up",
        ),
    )


def create_ramp_entry_dressup(
    document: Any,
    *,
    prepared: PreparedRampEntryDressup,
) -> NativeMutationDraft:
    """Create and configure one Ramp Entry replacement in the owned transaction."""

    if not isinstance(prepared, PreparedRampEntryDressup):
        raise TypeError("prepared must be a PreparedRampEntryDressup")
    assert_dressup_preflight_current(document, prepared.base)
    base = prepared.base
    try:
        import Path.Dressup.Gui.RampEntry as RampEntryGui

        operation = RampEntryGui.CreateInTransaction(base.base, hide_base=False)
        operation.Label = base.label
        operation.Method = prepared.internal_method
        operation.Angle = prepared.angle_from_vertical_degrees
        operation.UseStartDepth = prepared.start_depth_mm is not None
        if prepared.start_depth_mm is not None:
            operation.DressupStartDepth = prepared.start_depth_mm
        publish_dressup_replacement(document, base, operation)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Ramp Entry factory could not create the requested operation.",
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


def verify_created_ramp_entry_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact ramp motion, settings, and replacement History state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedRampEntryDressup) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Ramp Entry dress-up")
    base = prepared.base

    import Path.Dressup.Gui.RampEntry as RampEntryGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=RampEntryGui.ObjectDressup,
        view_proxy_type=RampEntryGui.ViewProviderDressup,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
    )
    actual_counts = _metadata_counts(operation.Proxy.lastGenerationStats)
    expected_counts = {
        name: getattr(prepared, name) for name in actual_counts
    }
    actual_center = tuple(round(float(item), 9) for item in operation.Path.Center)
    expected_center = tuple(round(float(item), 9) for item in base.job.Path.Center)
    if (
        str(operation.Method) != prepared.internal_method
        or round(float(operation.Angle.Value), 9)
        != round(prepared.angle_from_vertical_degrees, 9)
        or bool(operation.UseStartDepth) != (prepared.start_depth_mm is not None)
        or (
            prepared.start_depth_mm is not None
            and round(float(operation.DressupStartDepth.Value), 9)
            != round(prepared.start_depth_mm, 9)
        )
        or actual_counts != expected_counts
        or actual_center != expected_center
    ):
        dressup_error(
            "The created CAM Ramp Entry did not retain its exact method, activation, "
            "generation counts, or rotary center.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "ramp_entry_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "method": prepared.public_method,
        "angle_from_vertical_degrees": prepared.angle_from_vertical_degrees,
        "activation": (
            {"kind": "all_plunges"}
            if prepared.start_depth_mm is None
            else {"kind": "below_absolute_z", "z_mm": prepared.start_depth_mm}
        ),
        "ramped_plunge_count": prepared.ramped_plunge_count,
        "unchanged_plunge_count": prepared.unchanged_plunge_count,
        "ramp_motion_count": prepared.ramp_motion_count,
        "start_depth_split_count": prepared.start_depth_split_count,
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "path_center_mm": list(actual_center),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
