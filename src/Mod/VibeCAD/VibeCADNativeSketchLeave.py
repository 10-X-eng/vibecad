# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact task-owned transition out of one active Sketch edit session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADEditState import active_edit_object
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
)
from VibeCADNativeTargets import object_reference, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedSketchLeave:
    target: PreparedActiveSketchTarget


def prepare_sketch_leave(
    context: NativeRuntimeContext,
    spec: ActiveSketchTargetSpec,
) -> PreparedSketchLeave:
    return PreparedSketchLeave(preflight_active_sketch(context, spec))


def _counts(sketch: Any) -> tuple[int, int]:
    try:
        return int(sketch.GeometryCount), int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError(
            "The exact Sketch counts became unavailable while leaving edit mode."
        ) from exc


def leave_sketch_edit(
    context: NativeRuntimeContext,
    prepared: PreparedSketchLeave,
    *,
    sketcher_gui: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    if not isinstance(prepared, PreparedSketchLeave):
        raise TypeError("prepared must be a PreparedSketchLeave")

    context.guard()
    document = context.document
    sketch = prepared.target.sketch
    reference = prepared.target.spec.reference
    if resolve_object(
        document,
        reference,
        expected_types=("Sketcher::SketchObject",),
    ) is not sketch:
        raise NativeSketchError("The exact active Sketch changed before leaving.")

    if sketcher_gui is None:
        import SketcherGui as sketcher_gui

    leave = getattr(sketcher_gui, "leaveActiveSketch", None)
    if not callable(leave):
        raise NativeSketchError("Exact Sketch edit control is unavailable.")
    try:
        native_result = leave(
            str(document.Name),
            context.document_uid,
            reference.object_name,
        )
    except Exception as exc:
        raise NativeSketchError("Sketcher could not finish the exact active Sketch.") from exc
    if not isinstance(native_result, Mapping) or set(native_result) != {
        "document_name",
        "document_uid",
        "sketch_name",
        "accepted_task_dialog",
        "edit_mode",
    }:
        raise NativeSketchError("Sketcher returned an invalid edit-control result.")
    if (
        str(native_result["document_name"]) != str(document.Name)
        or str(native_result["document_uid"]) != context.document_uid
        or str(native_result["sketch_name"]) != reference.object_name
        or native_result["edit_mode"] != "closed"
    ):
        raise NativeSketchError("The exact Sketch identity changed while leaving.")

    if context.active_document() is not document:
        raise NativeSketchError("The exact Sketch document changed while leaving.")
    if resolve_object(
        document,
        reference,
        expected_types=("Sketcher::SketchObject",),
    ) is not sketch:
        raise NativeSketchError("The exact Sketch changed while leaving edit mode.")
    if active_edit_object() is not None or bool(context.edit_or_task_active()):
        raise NativeSketchError("The exact Sketch edit task did not close.")
    next_surface = str(context.active_surface_id() or "").strip()
    if not next_surface or next_surface == "sketch.edit":
        raise NativeSketchError("The Sketch ribbon did not leave edit mode.")

    geometry_count, constraint_count = _counts(sketch)
    if geometry_count != prepared.target.spec.expected_geometry_count:
        raise NativeSketchError("Leaving Sketch unexpectedly changed geometry count.")
    if constraint_count != prepared.target.spec.expected_constraint_count:
        raise NativeSketchError("Leaving Sketch unexpectedly changed constraint count.")
    try:
        valid = bool(sketch.isValid())
    except Exception as exc:
        raise NativeSketchError(
            "The exact Sketch validity became unavailable after leaving."
        ) from exc
    if not valid:
        raise NativeSketchError("The exact Sketch is invalid after leaving edit mode.")

    return {
        "operation": "leave",
        "sketch": object_reference(sketch),
        "geometry_count": geometry_count,
        "constraint_count": constraint_count,
        "edit_mode": "closed",
        "next_surface": next_surface,
        "next_turn_required": True,
    }
