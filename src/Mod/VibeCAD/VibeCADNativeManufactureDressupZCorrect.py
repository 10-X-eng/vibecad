# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-authorized, bounded CAM Z Correction dress-up creation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeInput import (
    NativeInputArtifact,
    NativeInputError,
    NativeInputRequest,
)
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
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


PROBE_MAP_SUFFIXES = (".txt", ".log", ".probe", ".dat")


@dataclass(frozen=True, slots=True)
class ZCorrectDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    arc_maximum_deflection_mm: Any
    line_maximum_segment_length_mm: Any


@dataclass(frozen=True, slots=True)
class ZCorrectBoundary:
    base: PreparedDressupBase
    definition: Any
    source_path: Any


@dataclass(frozen=True, slots=True)
class PreparedZCorrectDressup:
    boundary: ZCorrectBoundary
    artifact: NativeInputArtifact
    probe_grid: Any
    interpolation_shape: Any
    generated: Any
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str


def z_correct_input_request() -> NativeInputRequest:
    """Describe one existing probe map the human may explicitly authorize."""

    from Path.Dressup.ZCorrect import MAX_PROBE_BYTES

    return NativeInputRequest(
        purpose="cam_z_correction_probe_map",
        title="Select CAM Z Correction Probe Map",
        allowed_suffixes=PROBE_MAP_SUFFIXES,
        name_filter="Probe maps (*.txt *.log *.probe *.dat)",
        maximum_bytes=MAX_PROBE_BYTES,
    )


def preflight_z_correct_boundary(
    document: Any,
    spec: ZCorrectDressupSpec,
) -> ZCorrectBoundary:
    """Freeze the exact Job and detached toolpath before human file selection."""

    if not isinstance(spec, ZCorrectDressupSpec):
        raise TypeError("spec must be a ZCorrectDressupSpec")
    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM Z Correction dress-up",
    )
    try:
        import Path
        import PathScripts.PathUtils as PathUtils
        from Path.Dressup import ZCorrect as ZCorrectCore

        definition = ZCorrectCore.validate_definition(
            ZCorrectCore.ZCorrectionDefinition(
                arc_maximum_deflection_mm=spec.arc_maximum_deflection_mm,
                line_maximum_segment_length_mm=(
                    spec.line_maximum_segment_length_mm
                ),
            )
        )
        source = ZCorrectCore.freeze_toolpath(
            PathUtils.getPathWithPlacement(base.base),
            maximum_commands=ZCorrectCore.MAX_Z_CORRECT_INPUT_COMMANDS,
        )
    except NativeManufactureError:
        raise
    except Exception as exc:
        message = str(exc)
        code = (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
            if "limit" in message.lower()
            else "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
        )
        raise NativeManufactureError(
            "The exact CAM toolpath cannot receive probe-map Z Correction.",
            error_code=code,
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": message[:320],
            },
        ) from exc
    if not source.commands:
        dressup_error(
            "CAM Z Correction requires a nonempty exact toolpath.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    if any(command.name == "G91" for command in source.commands):
        dressup_error(
            "CAM Z Correction accepts only absolute-coordinate toolpaths; G91 was found.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    cutting = tuple(
        command for command in source.commands if command.name in Path.Geom.CmdMoveMill
    )
    if not cutting:
        dressup_error(
            "CAM Z Correction found no G1, G2, or G3 cutting move to correct.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    rotary = sorted(
        {
            name
            for command in cutting
            for name, _value in command.parameters
            if name in {"A", "B", "C", "U", "V", "W"}
        }
    )
    if rotary:
        dressup_error(
            "CAM Z Correction supports three-axis XYZ cutting paths only.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            repair={"rotary_coordinates": rotary},
        )
    return ZCorrectBoundary(base, definition, source)


def _cancelled(cancelled: Any) -> None:
    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()


def prepare_z_correct_dressup(
    boundary: ZCorrectBoundary,
    authorization: Any,
    request: NativeInputRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedZCorrectDressup:
    """Read, validate, and generate every detached result off the document thread."""

    if not isinstance(boundary, ZCorrectBoundary):
        raise TypeError("boundary must be a ZCorrectBoundary")
    _cancelled(cancelled)
    progress(5, "Verifying selected probe map")
    try:
        artifact = authorization.claim(request)
        content = artifact.read_bytes(maximum_bytes=request.maximum_bytes)
    except NativeInputError as exc:
        raise NativeManufactureError(str(exc), error_code=exc.code) from exc
    _cancelled(cancelled)
    progress(25, "Validating rectangular probe grid")
    try:
        from Path.Dressup import ZCorrect as ZCorrectCore

        grid = ZCorrectCore.parse_probe_bytes(content, strict=True)
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            "The selected probe map is not one complete rectangular XYZ grid.",
            error_code="NATIVE_MANUFACTURE_PROBE_MAP_INVALID",
            repair={
                "file_name": artifact.file_name,
                "native_error": str(exc)[:320],
            },
        ) from exc
    _cancelled(cancelled)
    progress(45, "Building detached interpolation surface")
    try:
        shape = ZCorrectCore.build_interpolation_surface(grid)
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            "The validated probe grid could not produce an interpolation surface.",
            error_code="NATIVE_MANUFACTURE_PROBE_MAP_INVALID",
            repair={"native_error": str(exc)[:320]},
        ) from exc
    _cancelled(cancelled)
    progress(65, "Generating bounded corrected toolpath")
    try:
        generated = ZCorrectCore.generate_corrected_path(
            boundary.source_path,
            shape,
            boundary.definition,
            maximum_output_commands=ZCorrectCore.MAX_Z_CORRECT_OUTPUT_COMMANDS,
        )
    except (TypeError, ValueError) as exc:
        message = str(exc)
        lowered = message.lower()
        code = (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
            if "limit" in lowered or "exceed" in lowered
            else "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
        )
        raise NativeManufactureError(
            "The exact toolpath could not be corrected by the selected probe map.",
            error_code=code,
            repair={"native_error": message[:320]},
        ) from exc
    if max(
        abs(float(generated.probe_offset_min_mm)),
        abs(float(generated.probe_offset_max_mm)),
    ) <= 1.0e-12:
        dressup_error(
            "The selected probe map applies zero Z correction along the exact cutting path.",
            "NATIVE_MANUFACTURE_NO_EFFECT",
        )
    commands = tuple(generated.path.Commands or ())
    expected_cutting = cutting_command_count(commands)
    if expected_cutting <= 0:
        dressup_error(
            "Probe-map correction produced no usable cutting motion.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    try:
        artifact.verify_unchanged()
    except NativeInputError as exc:
        raise NativeManufactureError(str(exc), error_code=exc.code) from exc
    _cancelled(cancelled)
    progress(88, "Corrected toolpath verified")
    return PreparedZCorrectDressup(
        boundary=boundary,
        artifact=artifact,
        probe_grid=grid,
        interpolation_shape=shape,
        generated=generated,
        expected_command_count=len(commands),
        expected_cutting_count=expected_cutting,
        expected_path_sha256=command_path_sha256(
            commands,
            "CAM Z Correction dress-up",
        ),
    )


def create_z_correct_dressup(
    document: Any,
    *,
    prepared: PreparedZCorrectDressup,
) -> NativeMutationDraft:
    """Commit one embedded, hash-pinned Z Correction replacement."""

    if not isinstance(prepared, PreparedZCorrectDressup):
        raise TypeError("prepared must be a PreparedZCorrectDressup")
    boundary = prepared.boundary
    base = boundary.base
    assert_dressup_preflight_current(document, base)
    try:
        prepared.artifact.host_path_after_content_verification()
        import Path.Dressup.Gui.ZCorrect as ZCorrectGui

        operation = ZCorrectGui.CreateInTransaction(base.base, hide_base=False)
        operation.Label = base.label
        operation.probefile = ""
        operation.interpSurface = prepared.interpolation_shape
        operation.ArcInterpolate = boundary.definition.arc_maximum_deflection_mm
        operation.SegInterpolate = boundary.definition.line_maximum_segment_length_mm
        operation.ProbeDataSHA256 = prepared.artifact.sha256
        operation.ProbePointCount = prepared.probe_grid.point_count
        operation.ProbeGridXCount = prepared.probe_grid.x_count
        operation.ProbeGridYCount = prepared.probe_grid.y_count
        operation.VibeCADExternalInputs = [prepared.artifact.file_name]
        operation.Path = prepared.generated.path
        publish_dressup_replacement(document, base, operation)
    except NativeManufactureError:
        raise
    except NativeInputError as exc:
        raise NativeManufactureError(str(exc), error_code=exc.code) from exc
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Z Correction factory could not create the requested operation.",
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


def _rounded_center(path: Any) -> tuple[float, float, float]:
    values = tuple(round(float(value), 9) for value in path.Center)
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        dressup_error(
            "The created Z Correction has an invalid rotary center.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return values


def verify_created_z_correct_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove the embedded probe identity, exact path, and replacement lifecycle."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedZCorrectDressup) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Z Correction")
    base = prepared.boundary.base

    import Path.Dressup.Gui.ZCorrect as ZCorrectGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=ZCorrectGui.ObjectDressup,
        view_proxy_type=ZCorrectGui.ViewProviderDressup,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
    )
    grid = prepared.probe_grid
    actual_center = _rounded_center(operation.Path)
    expected_center = _rounded_center(prepared.generated.path)
    source_center = tuple(
        round(float(value), 9)
        for value in prepared.boundary.source_path.center_mm
    )
    job_center = _rounded_center(base.job.Path)
    bounds = operation.interpSurface.BoundBox
    actual_bounds = tuple(
        round(float(value), 9)
        for value in (bounds.XMin, bounds.YMin, bounds.XMax, bounds.YMax)
    )
    expected_bounds = tuple(
        round(float(value), 9)
        for value in (grid.x_min_mm, grid.y_min_mm, grid.x_max_mm, grid.y_max_mm)
    )
    if (
        str(operation.probefile or "")
        or operation.interpSurface.isNull()
        or str(operation.ProbeDataSHA256) != prepared.artifact.sha256
        or int(operation.ProbePointCount) != grid.point_count
        or int(operation.ProbeGridXCount) != grid.x_count
        or int(operation.ProbeGridYCount) != grid.y_count
        or list(operation.VibeCADExternalInputs) != [prepared.artifact.file_name]
        or round(float(operation.ArcInterpolate.Value), 9)
        != round(prepared.boundary.definition.arc_maximum_deflection_mm, 9)
        or round(float(operation.SegInterpolate.Value), 9)
        != round(prepared.boundary.definition.line_maximum_segment_length_mm, 9)
        or actual_center != expected_center
        or actual_center != source_center
        or actual_center != job_center
        or actual_bounds != expected_bounds
    ):
        dressup_error(
            "The created CAM Z Correction did not retain its exact probe identity, "
            "surface, interpolation bounds, or rotary center.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    try:
        prepared.artifact.host_path_after_content_verification()
    except NativeInputError as exc:
        raise NativeManufactureError(str(exc), error_code=exc.code) from exc
    return {
        "operation": "z_correct_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "input": prepared.artifact.summary(),
        "probe_grid": {
            "point_count": grid.point_count,
            "x_count": grid.x_count,
            "y_count": grid.y_count,
            "bounds_xy_mm": list(expected_bounds),
            "measured_z_range_mm": [grid.z_min_mm, grid.z_max_mm],
        },
        "arc_maximum_deflection_mm": (
            prepared.boundary.definition.arc_maximum_deflection_mm
        ),
        "line_maximum_segment_length_mm": (
            prepared.boundary.definition.line_maximum_segment_length_mm
        ),
        "corrected_source_move_count": (
            prepared.generated.corrected_source_move_count
        ),
        "generated_linear_move_count": (
            prepared.generated.generated_linear_move_count
        ),
        "linearized_arc_count": prepared.generated.linearized_arc_count,
        "applied_z_offset_range_mm": [
            prepared.generated.probe_offset_min_mm,
            prepared.generated.probe_offset_max_mm,
        ],
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "path_center_mm": list(actual_center),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
