# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Mirror dress-up."""

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
from VibeCADNativeManufactureState import candidate_model_state, resolve_model_target
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_TARGET_FIELDS = frozenset({"object_name", "expected_state_sha256"})
_REFERENCE_FIELDS = frozenset(
    {"object_name", "expected_state_sha256", "subelement"}
)
_VECTOR_FIELDS = frozenset({"x_mm", "y_mm", "z_mm"})
_MIRROR_FIELDS = {
    "axis_at_origin": frozenset({"kind", "axis", "offset_mm", "keep_base_path"}),
    "axis_at_model_center": frozenset(
        {"kind", "axis", "model", "offset_mm", "keep_base_path"}
    ),
    "axis_aligned_reference": frozenset(
        {"kind", "reference", "offset_mm", "keep_base_path"}
    ),
}
_AXIS_NAMES = {"x": "X", "y": "Y", "xy": "XY"}


@dataclass(frozen=True, slots=True)
class MirrorDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    mirror: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedMirrorDressup:
    base: PreparedDressupBase
    kind: str
    axis: str
    offset_mm: tuple[float, float, float]
    keep_base_path: bool
    auxiliary_object: Any | None
    auxiliary_before: Mapping[str, Any] | None
    reference_subelement: str
    resolved_axis: str
    resolved_offset_mm: tuple[float, float, float]
    mirrored_command_count: int
    move_command_count: int
    arc_direction_swap_count: int
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str


def _strict_bool(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        dressup_error(f"{noun} must be a boolean.")
    return value


def _vector(value: Any, noun: str) -> tuple[float, float, float]:
    item = exact_fields(value, _VECTOR_FIELDS, noun)
    return tuple(
        finite_number(item[f"{axis}_mm"], f"{noun} {axis}")
        for axis in ("x", "y", "z")
    )


def _target(value: Any, noun: str) -> Mapping[str, Any]:
    item = exact_fields(value, _TARGET_FIELDS, noun)
    return {
        "object_name": item["object_name"],
        "expected_state_sha256": item["expected_state_sha256"],
    }


def _normalize_mirror(
    document: Any,
    base: PreparedDressupBase,
    value: Any,
):
    if not isinstance(value, Mapping):
        dressup_error("CAM Mirror mirror must be one closed placement request.")
    kind = str(value.get("kind") or "")
    fields = _MIRROR_FIELDS.get(kind)
    if fields is None:
        dressup_error(
            "CAM Mirror kind must be axis_at_origin, axis_at_model_center, or "
            "axis_aligned_reference."
        )
    item = exact_fields(value, fields, f"CAM Mirror {kind}")
    keep = _strict_bool(item["keep_base_path"], "CAM Mirror keep_base_path")
    offset = _vector(item["offset_mm"], "CAM Mirror offset_mm")
    auxiliary = None
    auxiliary_before = None
    reference_name = ""
    if kind == "axis_aligned_reference":
        reference = exact_fields(
            item["reference"],
            _REFERENCE_FIELDS,
            "CAM Mirror reference",
        )
        reference_name = str(reference["subelement"] or "").strip()
        if (
            len(reference_name) > 80
            or not reference_name.startswith(("Edge", "Face"))
            or not reference_name[len("Edge") if reference_name.startswith("Edge") else len("Face") :].isdigit()
        ):
            dressup_error("CAM Mirror reference subelement must be one EdgeN or FaceN.")
        auxiliary, auxiliary_before = resolve_model_target(
            document,
            {
                "object_name": reference["object_name"],
                "expected_state_sha256": reference["expected_state_sha256"],
            },
        )
        axis = "Reference"
    else:
        native_axis = str(item["axis"] or "")
        if native_axis not in _AXIS_NAMES:
            dressup_error("CAM Mirror axis must be x, y, or xy.")
        axis = _AXIS_NAMES[native_axis]
        if kind == "axis_at_model_center":
            auxiliary, auxiliary_before = resolve_model_target(
                document,
                _target(item["model"], "CAM Mirror model"),
            )
            available_models = {
                str(model.get("object_name") or "")
                for model in base.job_before.get("models", ())
            }
            if str(auxiliary.Name) not in available_models:
                dressup_error(
                    "CAM Mirror model must be one exact current model in the target Job.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                    repair={"available_model_names": sorted(available_models)},
                )
    return kind, axis, offset, keep, auxiliary, auxiliary_before, reference_name


def _counts(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "resolved_axis": str(metadata["resolved_axis"]),
        "resolved_offset_mm": tuple(
            round(float(value), 9) for value in metadata["resolved_offset_mm"]
        ),
        "mirrored_command_count": int(metadata["mirrored_command_count"]),
        "move_command_count": int(metadata["move_command_count"]),
        "arc_direction_swap_count": int(metadata["arc_direction_swap_count"]),
    }


def preflight_mirror_dressup(
    document: Any,
    spec: MirrorDressupSpec,
) -> PreparedMirrorDressup:
    """Freeze one exact base and prepare its complete detached mirror."""

    if not isinstance(spec, MirrorDressupSpec):
        raise TypeError("spec must be a MirrorDressupSpec")
    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM Mirror dress-up",
    )
    (
        kind,
        axis,
        offset,
        keep,
        auxiliary,
        auxiliary_before,
        reference_name,
    ) = _normalize_mirror(document, base, spec.mirror)
    try:
        import Path.Dressup.Mirror as Mirror
        import PathScripts.PathUtils as PathUtils

        expected_path, metadata = Mirror.generatePathWithMetadata(
            base.base,
            Mirror.MirrorDefinition(
                axis=axis,
                offset_mm=offset,
                keep_base_path=keep,
                center_model=(
                    auxiliary if kind == "axis_at_model_center" else None
                ),
                reference_object=(
                    auxiliary if kind == "axis_aligned_reference" else None
                ),
                reference_subelement=reference_name,
            ),
        )
        commands = tuple(expected_path.Commands or ())
        source_commands = tuple(
            PathUtils.getPathWithPlacement(base.base).Commands or ()
        )
        counts = _counts(metadata)
    except NativeManufactureError:
        raise
    except Exception as exc:
        code = (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
            if "limit" in str(exc).lower()
            else "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        raise NativeManufactureError(
            "The exact CAM Mirror toolpath could not be prepared.",
            error_code=code,
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    cutting = cutting_command_count(commands)
    if cutting <= 0:
        dressup_error(
            "CAM Mirror did not retain a usable cutting path.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    expected_hash = command_path_sha256(commands, "CAM Mirror dress-up")
    if not keep and expected_hash == command_path_sha256(
        source_commands,
        "CAM Mirror base",
    ):
        dressup_error(
            "CAM Mirror settings do not change the exact base toolpath.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return PreparedMirrorDressup(
        base=base,
        kind=kind,
        axis=axis,
        offset_mm=offset,
        keep_base_path=keep,
        auxiliary_object=auxiliary,
        auxiliary_before=auxiliary_before,
        reference_subelement=reference_name,
        resolved_axis=counts["resolved_axis"],
        resolved_offset_mm=counts["resolved_offset_mm"],
        mirrored_command_count=counts["mirrored_command_count"],
        move_command_count=counts["move_command_count"],
        arc_direction_swap_count=counts["arc_direction_swap_count"],
        expected_command_count=len(commands),
        expected_cutting_count=cutting,
        expected_path_sha256=expected_hash,
    )


def _assert_auxiliary_current(prepared: PreparedMirrorDressup) -> None:
    if prepared.auxiliary_object is None:
        return
    current = candidate_model_state(prepared.auxiliary_object)
    if current.get("state_sha256") != prepared.auxiliary_before.get("state_sha256"):
        dressup_error(
            "The exact CAM Mirror model or reference changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def create_mirror_dressup(
    document: Any,
    *,
    prepared: PreparedMirrorDressup,
) -> NativeMutationDraft:
    """Create and configure one Mirror replacement in the owned transaction."""

    if not isinstance(prepared, PreparedMirrorDressup):
        raise TypeError("prepared must be a PreparedMirrorDressup")
    assert_dressup_preflight_current(document, prepared.base)
    _assert_auxiliary_current(prepared)
    base = prepared.base
    try:
        import FreeCAD
        import Path.Dressup.Gui.Mirror as MirrorGui

        operation = MirrorGui.CreateInTransaction(base.base, hide_base=False)
        operation.Label = base.label
        operation.KeepBasePath = prepared.keep_base_path
        operation.Offset = FreeCAD.Vector(*prepared.offset_mm)
        operation.CenterModel = prepared.kind == "axis_at_model_center"
        operation.CenterModelReference = (
            prepared.auxiliary_object
            if prepared.kind == "axis_at_model_center"
            else None
        )
        if prepared.kind == "axis_aligned_reference":
            operation.MirrorAxis = "Reference"
            operation.Reference = (
                prepared.auxiliary_object,
                [prepared.reference_subelement],
            )
        else:
            operation.MirrorAxis = prepared.axis
        publish_dressup_replacement(document, base, operation)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Mirror factory could not create the requested operation.",
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


def verify_created_mirror_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact mirrored motion and replacement History state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedMirrorDressup) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Mirror dress-up")
    base = prepared.base

    import Path.Dressup.Gui.Mirror as MirrorGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=MirrorGui.ObjectDressup,
        view_proxy_type=MirrorGui.ViewProviderDressup,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
    )
    actual_counts = _counts(operation.Proxy.lastGenerationStats)
    expected_counts = {
        "resolved_axis": prepared.resolved_axis,
        "resolved_offset_mm": prepared.resolved_offset_mm,
        "mirrored_command_count": prepared.mirrored_command_count,
        "move_command_count": prepared.move_command_count,
        "arc_direction_swap_count": prepared.arc_direction_swap_count,
    }
    actual_reference = getattr(operation, "Reference", None)
    reference_ok = True
    if prepared.kind == "axis_aligned_reference":
        reference_ok = bool(
            actual_reference
            and actual_reference[0] is prepared.auxiliary_object
            and tuple(actual_reference[1] or ()) == (prepared.reference_subelement,)
        )
    expected_center = tuple(round(float(item), 9) for item in base.job.Path.Center)
    actual_center = tuple(round(float(item), 9) for item in operation.Path.Center)
    expected_axis = "Reference" if prepared.kind == "axis_aligned_reference" else prepared.axis
    if (
        str(operation.MirrorAxis) != expected_axis
        or tuple(round(float(item), 9) for item in operation.Offset)
        != prepared.offset_mm
        or bool(operation.KeepBasePath) != prepared.keep_base_path
        or bool(operation.CenterModel) != (prepared.kind == "axis_at_model_center")
        or (
            prepared.kind == "axis_at_model_center"
            and operation.CenterModelReference is not prepared.auxiliary_object
        )
        or not reference_ok
        or actual_counts != expected_counts
        or actual_center != expected_center
    ):
        dressup_error(
            "The created CAM Mirror did not retain its exact placement, generation "
            "counts, or rotary center.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "mirror_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "kind": prepared.kind,
        "axis": prepared.resolved_axis.lower(),
        "offset_mm": list(prepared.offset_mm),
        "resolved_offset_mm": list(prepared.resolved_offset_mm),
        "keep_base_path": prepared.keep_base_path,
        "reference_object_name": (
            str(prepared.auxiliary_object.Name)
            if prepared.kind == "axis_aligned_reference"
            else None
        ),
        "reference_subelement": prepared.reference_subelement or None,
        "mirrored_command_count": prepared.mirrored_command_count,
        "move_command_count": prepared.move_command_count,
        "arc_direction_swap_count": prepared.arc_direction_swap_count,
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "path_center_mm": list(actual_center),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
