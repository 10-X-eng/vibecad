# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact optional or mandatory CAM program-stop creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureProgram import (
    PreparedProgramBoundary,
    assert_program_boundary_current,
    preflight_program_boundary,
    program_error,
    program_label,
    program_mutation_draft,
    verify_program_operation,
)
from VibeCADNativeMutation import NativeMutationDraft


STOP_MODES = frozenset({"optional", "mandatory"})
_HUMAN_MODE = {"optional": "Optional", "mandatory": "Mandatory"}
_GCODE = {"optional": "M1", "mandatory": "M0"}


@dataclass(frozen=True, slots=True)
class StopCreateSpec:
    label: Any
    job: Mapping[str, Any]
    stop_mode: Any


@dataclass(frozen=True, slots=True)
class PreparedStopCreate:
    label: str
    stop_mode: str
    boundary: PreparedProgramBoundary


def _stop_mode(value: Any) -> str:
    if not isinstance(value, str) or value not in STOP_MODES:
        program_error(
            "stop_mode must be exactly 'optional' or 'mandatory'.",
            repair={
                "field": "stop_mode",
                "allowed_values": ["optional", "mandatory"],
            },
        )
    return value


def preflight_stop_create(
    document: Any,
    spec: StopCreateSpec,
) -> PreparedStopCreate:
    if not isinstance(spec, StopCreateSpec):
        raise TypeError("spec must be a StopCreateSpec")
    return PreparedStopCreate(
        label=program_label(spec.label),
        stop_mode=_stop_mode(spec.stop_mode),
        boundary=preflight_program_boundary(
            document,
            spec.job,
            noun="CAM stop",
        ),
    )


def create_stop(
    document: Any,
    *,
    prepared: PreparedStopCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedStopCreate):
        raise TypeError("prepared must be a PreparedStopCreate")
    boundary = prepared.boundary
    assert_program_boundary_current(document, boundary)
    try:
        import Path.Op.Gui.Stop as StopGui

        operation = StopGui.CreateInTransaction(
            document,
            boundary.job,
            name="Stop",
            mode=_HUMAN_MODE[prepared.stop_mode],
        )
        operation.Label = prepared.label
        StopGui._validate_stop_result(
            document,
            boundary.job,
            operation,
            require_path=False,
        )
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM stop factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return program_mutation_draft(
        boundary,
        operation,
        value={"prepared": prepared},
    )


def verify_created_stop(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedStopCreate) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM stop")

    import Path.Op.Gui.Stop as StopGui

    state, after_job = verify_program_operation(
        document,
        prepared.boundary,
        operation,
        label=prepared.label,
        proxy_type=StopGui.Stop,
        view_proxy_type=StopGui._ViewProviderStop,
    )
    commands = tuple(getattr(operation.Path, "Commands", ()) or ())
    expected_gcode = _GCODE[prepared.stop_mode]
    if (
        str(operation.Stop) != _HUMAN_MODE[prepared.stop_mode]
        or len(commands) != 1
        or str(commands[0].Name) != expected_gcode
        or dict(commands[0].Parameters) != {}
        or str(commands[0].toGCode()) != expected_gcode
    ):
        program_error(
            "The created CAM stop did not retain its exact stop mode and command.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "stop",
        "object_name": str(operation.Name),
        "label": str(operation.Label)[:160],
        "job_object_name": str(prepared.boundary.job.Name),
        "stop_mode": prepared.stop_mode,
        "command_count": 1,
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
