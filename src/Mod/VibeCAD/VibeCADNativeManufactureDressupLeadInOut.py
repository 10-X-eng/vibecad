# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of every shipped CAM Lead In/Out dress-up."""

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
from VibeCADNativeManufactureOperationSupport import exact_fields, finite_number
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_LEAD_IN_OUT_INPUT_COMMANDS = 50_000
_STYLE_NAMES = {
    "arc": "Arc",
    "line": "Line",
    "perpendicular": "Perpendicular",
    "tangent": "Tangent",
    "arc_3d": "Arc3d",
    "arc_z": "ArcZ",
    "arc_z_follow": "ArcZFollow",
    "helix": "Helix",
    "line_3d": "Line3d",
    "line_z": "LineZ",
    "line_z_follow": "LineZFollow",
    "no_retract": "No Retract",
    "vertical": "Vertical",
}
_STYLE_FIELDS = {
    "disabled": frozenset({"style"}),
    "arc": frozenset(
        {"style", "angle_degrees", "radius_mm", "invert", "offset_mm", "extend_mm"}
    ),
    "line": frozenset(
        {"style", "angle_degrees", "length_mm", "invert", "offset_mm", "extend_mm"}
    ),
    "perpendicular": frozenset({"style", "length_mm", "offset_mm", "extend_mm"}),
    "tangent": frozenset({"style", "length_mm", "offset_mm", "extend_mm"}),
    "arc_3d": frozenset({"style", "angle_degrees", "radius_mm", "invert", "offset_mm"}),
    "arc_z": frozenset({"style", "angle_degrees", "radius_mm", "offset_mm"}),
    "arc_z_follow": frozenset({"style", "angle_degrees", "radius_mm", "offset_mm"}),
    "helix": frozenset({"style", "angle_degrees", "radius_mm", "invert", "offset_mm"}),
    "line_3d": frozenset(
        {"style", "angle_degrees", "length_mm", "invert", "offset_mm"}
    ),
    "line_z": frozenset({"style", "angle_degrees", "length_mm", "offset_mm"}),
    "line_z_follow": frozenset({"style", "angle_degrees", "length_mm", "offset_mm"}),
    "no_retract": frozenset({"style"}),
    "vertical": frozenset({"style", "offset_mm"}),
}


@dataclass(frozen=True, slots=True)
class LeadInOutDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    lead_in: Mapping[str, Any]
    lead_out: Mapping[str, Any]
    retract_threshold_mm: Any
    rapid_plunge: Any


@dataclass(frozen=True, slots=True)
class PreparedLeadInOutDressup:
    base: PreparedDressupBase
    lead_in: Any
    lead_out: Any
    retract_threshold_mm: float
    rapid_plunge: bool
    profile_count: int
    closed_profile_count: int
    open_profile_count: int
    lead_in_profile_count: int
    lead_out_profile_count: int
    command_delta: int
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str


def _strict_bool(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        dressup_error(f"{noun} must be a boolean.")
    return value


def _normalize_lead(value: Any, noun: str):
    if not isinstance(value, Mapping):
        dressup_error(f"{noun} must be one closed lead-style request.")
    style = str(value.get("style") or "")
    fields = _STYLE_FIELDS.get(style)
    if fields is None:
        dressup_error(
            f"{noun} style must be disabled or one of: {', '.join(_STYLE_NAMES)}."
        )
    item = exact_fields(value, fields, noun)
    try:
        import Path.Dressup.LeadInOut as LeadInOut

        if style == "disabled":
            definition = LeadInOut.LeadDefinition(None)
        else:
            radius_field = "radius_mm" if "radius_mm" in fields else "length_mm"
            radius = (
                finite_number(
                    item[radius_field],
                    f"{noun} {radius_field}",
                    minimum=0.0,
                    maximum=1_000_000.0,
                )
                if radius_field in fields
                else 0.0
            )
            if radius_field in fields and radius <= 0.0:
                dressup_error(f"{noun} {radius_field} must be greater than zero.")
            definition = LeadInOut.LeadDefinition(
                style=_STYLE_NAMES[style],
                angle_degrees=(
                    finite_number(
                        item["angle_degrees"],
                        f"{noun} angle_degrees",
                        minimum=0.0,
                        maximum=180.0,
                    )
                    if "angle_degrees" in fields
                    else 0.0
                ),
                radius_or_length_mm=radius,
                invert=(
                    _strict_bool(item["invert"], f"{noun} invert")
                    if "invert" in fields
                    else False
                ),
                offset_mm=(
                    finite_number(item["offset_mm"], f"{noun} offset_mm")
                    if "offset_mm" in fields
                    else 0.0
                ),
                extend_mm=(
                    finite_number(
                        item["extend_mm"],
                        f"{noun} extend_mm",
                        minimum=0.0,
                        maximum=1_000_000.0,
                    )
                    if "extend_mm" in fields
                    else 0.0
                ),
            )
        return LeadInOut.normalize_lead_definition(definition, noun)
    except NativeManufactureError:
        raise
    except (ArithmeticError, TypeError, ValueError) as exc:
        dressup_error(str(exc))


def _generation_counts(metadata: Mapping[str, Any]) -> dict[str, int]:
    return {
        name: int(metadata[name])
        for name in (
            "profile_count",
            "closed_profile_count",
            "open_profile_count",
            "lead_in_profile_count",
            "lead_out_profile_count",
        )
    }


def _lead_result(definition: Any) -> dict[str, Any]:
    if definition.style is None:
        return {"style": "disabled"}
    native_style = next(
        key for key, value in _STYLE_NAMES.items() if value == definition.style
    )
    result = {"style": native_style}
    if definition.style in (
        "Arc",
        "Line",
        "Arc3d",
        "ArcZ",
        "ArcZFollow",
        "Helix",
        "Line3d",
        "LineZ",
        "LineZFollow",
    ):
        result["angle_degrees"] = definition.angle_degrees
    if definition.style in ("Arc", "Arc3d", "ArcZ", "ArcZFollow", "Helix"):
        result["radius_mm"] = definition.radius_or_length_mm
    elif definition.style not in ("No Retract", "Vertical"):
        result["length_mm"] = definition.radius_or_length_mm
    if definition.style in ("Arc", "Line", "Arc3d", "Helix", "Line3d"):
        result["invert"] = definition.invert
    if definition.style != "No Retract":
        result["offset_mm"] = definition.offset_mm
    if definition.style in ("Arc", "Line", "Perpendicular", "Tangent"):
        result["extend_mm"] = definition.extend_mm
    return result


def preflight_lead_in_out_dressup(
    document: Any,
    spec: LeadInOutDressupSpec,
) -> PreparedLeadInOutDressup:
    """Freeze one exact base and prepare its complete entry/exit motion."""

    if not isinstance(spec, LeadInOutDressupSpec):
        raise TypeError("spec must be a LeadInOutDressupSpec")
    lead_in = _normalize_lead(spec.lead_in, "CAM Lead In/Out lead_in")
    lead_out = _normalize_lead(spec.lead_out, "CAM Lead In/Out lead_out")
    if lead_in.style is None and lead_out.style is None:
        dressup_error("CAM Lead In/Out must enable lead_in, lead_out, or both.")
    threshold = finite_number(
        spec.retract_threshold_mm,
        "CAM Lead In/Out retract_threshold_mm",
        minimum=0.0,
        maximum=1_000_000.0,
    )
    rapid_plunge = _strict_bool(spec.rapid_plunge, "CAM Lead In/Out rapid_plunge")
    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM Lead In/Out dress-up",
    )
    input_commands = tuple(base.base.Path.Commands or ())
    if len(input_commands) > MAX_LEAD_IN_OUT_INPUT_COMMANDS:
        dressup_error(
            f"CAM Lead In/Out base has {len(input_commands)} commands; its interactive "
            f"limit is {MAX_LEAD_IN_OUT_INPUT_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    try:
        import Path.Dressup.LeadInOut as LeadInOut
        import PathScripts.PathUtils as PathUtils

        expected_path, metadata = LeadInOut.generatePathWithMetadata(
            base.base,
            lead_in=lead_in,
            lead_out=lead_out,
            retract_threshold_mm=threshold,
            rapid_plunge=rapid_plunge,
            max_output_commands=MAX_DRESSUP_COMMANDS,
        )
        commands = tuple(expected_path.Commands or ())
        placed_commands = tuple(
            PathUtils.getPathWithPlacement(base.base).Commands or ()
        )
        counts = _generation_counts(metadata)
    except NativeManufactureError:
        raise
    except Exception as exc:
        code = (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
            if "limit" in str(exc).lower() or "more than" in str(exc).lower()
            else "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
        )
        raise NativeManufactureError(
            "The exact CAM Lead In/Out toolpath could not be prepared.",
            error_code=code,
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    if len(commands) > MAX_DRESSUP_COMMANDS:
        dressup_error(
            f"CAM Lead In/Out would generate {len(commands)} commands; the safety "
            f"limit is {MAX_DRESSUP_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    cutting = cutting_command_count(commands)
    if counts["profile_count"] <= 0 or cutting <= 0:
        dressup_error(
            "CAM Lead In/Out base must contain at least one usable cutting profile.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    expected_hash = command_path_sha256(commands, "CAM Lead In/Out dress-up")
    base_hash = command_path_sha256(placed_commands, "CAM Lead In/Out base")
    if expected_hash == base_hash:
        dressup_error(
            "CAM Lead In/Out settings do not change the exact base toolpath.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return PreparedLeadInOutDressup(
        base=base,
        lead_in=lead_in,
        lead_out=lead_out,
        retract_threshold_mm=threshold,
        rapid_plunge=rapid_plunge,
        profile_count=counts["profile_count"],
        closed_profile_count=counts["closed_profile_count"],
        open_profile_count=counts["open_profile_count"],
        lead_in_profile_count=counts["lead_in_profile_count"],
        lead_out_profile_count=counts["lead_out_profile_count"],
        command_delta=len(commands) - len(placed_commands),
        expected_command_count=len(commands),
        expected_cutting_count=cutting,
        expected_path_sha256=expected_hash,
    )


def _apply_lead(operation: Any, suffix: str, definition: Any) -> None:
    setattr(operation, f"Lead{suffix}", definition.style is not None)
    if definition.style is None:
        return
    setattr(operation, f"Style{suffix}", definition.style)
    setattr(operation, f"Angle{suffix}", definition.angle_degrees)
    operation.setExpression(f"Radius{suffix}", None)
    setattr(operation, f"Radius{suffix}", definition.radius_or_length_mm)
    setattr(operation, f"Invert{suffix}", definition.invert)
    setattr(operation, f"Offset{suffix}", definition.offset_mm)
    setattr(operation, f"ExtendLead{suffix}", definition.extend_mm)


def create_lead_in_out_dressup(
    document: Any,
    *,
    prepared: PreparedLeadInOutDressup,
) -> NativeMutationDraft:
    """Create and configure one Lead In/Out replacement in the owned transaction."""

    if not isinstance(prepared, PreparedLeadInOutDressup):
        raise TypeError("prepared must be a PreparedLeadInOutDressup")
    assert_dressup_preflight_current(document, prepared.base)
    base = prepared.base
    try:
        import Path.Dressup.Gui.LeadInOut as LeadInOutGui

        operation = LeadInOutGui.CreateInTransaction(
            base.base,
            hide_base=False,
        )
        operation.Label = base.label
        _apply_lead(operation, "In", prepared.lead_in)
        _apply_lead(operation, "Out", prepared.lead_out)
        operation.RetractThreshold = prepared.retract_threshold_mm
        operation.RapidPlunge = prepared.rapid_plunge
        publish_dressup_replacement(document, base, operation)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Lead In/Out factory could not create the requested operation.",
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


def verify_created_lead_in_out_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact lead geometry and replacement History state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedLeadInOutDressup) or operation is None:
        raise TypeError(
            "draft must contain one exact prepared CAM Lead In/Out dress-up"
        )
    base = prepared.base

    import Path.Dressup.LeadInOut as LeadInOut
    import Path.Dressup.Gui.LeadInOut as LeadInOutGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=LeadInOutGui.ObjectDressup,
        view_proxy_type=LeadInOutGui.ViewProviderDressup,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
    )
    actual_in = LeadInOut.lead_definition_from_object(operation, "In", clamp=False)
    actual_out = LeadInOut.lead_definition_from_object(operation, "Out", clamp=False)
    actual_counts = _generation_counts(operation.Proxy.lastGenerationStats)
    expected_counts = {
        "profile_count": prepared.profile_count,
        "closed_profile_count": prepared.closed_profile_count,
        "open_profile_count": prepared.open_profile_count,
        "lead_in_profile_count": prepared.lead_in_profile_count,
        "lead_out_profile_count": prepared.lead_out_profile_count,
    }
    expected_center = tuple(round(float(item), 9) for item in base.job.Path.Center)
    actual_center = tuple(round(float(item), 9) for item in operation.Path.Center)
    if (
        actual_in != prepared.lead_in
        or actual_out != prepared.lead_out
        or round(float(operation.RetractThreshold.Value), 9)
        != prepared.retract_threshold_mm
        or bool(operation.RapidPlunge) != prepared.rapid_plunge
        or actual_counts != expected_counts
        or actual_center != expected_center
    ):
        dressup_error(
            "The created CAM Lead In/Out did not retain its exact settings, generation "
            "counts, or rotary center.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "lead_in_out_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "lead_in": _lead_result(prepared.lead_in),
        "lead_out": _lead_result(prepared.lead_out),
        "retract_threshold_mm": prepared.retract_threshold_mm,
        "rapid_plunge": prepared.rapid_plunge,
        "profile_count": prepared.profile_count,
        "closed_profile_count": prepared.closed_profile_count,
        "open_profile_count": prepared.open_profile_count,
        "lead_in_profile_count": prepared.lead_in_profile_count,
        "lead_out_profile_count": prepared.lead_out_profile_count,
        "command_delta": prepared.command_delta,
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "path_center_mm": list(actual_center),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
