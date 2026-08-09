# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact human-opened Sketch target guards shared by Native Sketch tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADEditState import active_edit_object
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeTargets import NativeObjectRef, resolve_object


MAX_SKETCH_ELEMENTS = 1_000_000


@dataclass(frozen=True, slots=True)
class ActiveSketchTargetSpec:
    reference: NativeObjectRef
    expected_geometry_count: int
    expected_constraint_count: int


@dataclass(frozen=True, slots=True)
class PreparedActiveSketchTarget:
    spec: ActiveSketchTargetSpec
    sketch: Any
    context: NativeRuntimeContext


def _expected_count(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"Sketch {label} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def prepare_active_sketch_target(
    document_uid: str,
    *,
    sketch: Mapping[str, Any],
    expected_geometry_count: Any,
    expected_constraint_count: Any,
) -> ActiveSketchTargetSpec:
    if not isinstance(sketch, Mapping) or set(sketch) != {"object_name"}:
        raise NativeSketchError("The active Sketch target has incorrect fields.")
    return ActiveSketchTargetSpec(
        NativeObjectRef(str(document_uid or ""), str(sketch["object_name"] or "")),
        _expected_count(expected_geometry_count, "geometry count"),
        _expected_count(expected_constraint_count, "constraint count"),
    )


def _current_counts(sketch: Any) -> tuple[int, int]:
    try:
        return int(sketch.GeometryCount), int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError(
            "The active Sketch does not expose stable geometry and constraint counts."
        ) from exc


def require_prepared_active_sketch(
    document: Any,
    prepared: PreparedActiveSketchTarget,
) -> Any:
    if not isinstance(prepared, PreparedActiveSketchTarget):
        raise TypeError("prepared must be a PreparedActiveSketchTarget")
    prepared.context.guard()
    if prepared.context.document is not document:
        raise NativeSketchError("The exact Sketch document changed during the operation.")
    if (
        str(prepared.context.active_surface_id() or "") != "sketch.edit"
        or not bool(prepared.context.edit_or_task_active())
    ):
        raise NativeSketchError(
            "The human must keep the exact Sketch open on the Sketch ribbon."
        )
    sketch = resolve_object(
        document,
        prepared.spec.reference,
        expected_types=("Sketcher::SketchObject",),
    )
    if sketch is not prepared.sketch or active_edit_object() is not sketch:
        raise NativeSketchError("The human-opened Sketch is not the exact requested target.")
    return sketch


def preflight_active_sketch(
    context: NativeRuntimeContext,
    spec: ActiveSketchTargetSpec,
) -> PreparedActiveSketchTarget:
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    if not isinstance(spec, ActiveSketchTargetSpec):
        raise TypeError("spec must be an ActiveSketchTargetSpec")
    context.guard()
    sketch = resolve_object(
        context.document,
        spec.reference,
        expected_types=("Sketcher::SketchObject",),
    )
    prepared = PreparedActiveSketchTarget(spec, sketch, context)
    require_prepared_active_sketch(context.document, prepared)
    geometry_count, constraint_count = _current_counts(sketch)
    if geometry_count != spec.expected_geometry_count:
        raise NativeSketchError(
            "The active Sketch geometry count changed; read its current state and retry."
        )
    if constraint_count != spec.expected_constraint_count:
        raise NativeSketchError(
            "The active Sketch constraint count changed; read its current state and retry."
        )
    try:
        valid = bool(sketch.isValid())
    except Exception as exc:
        raise NativeSketchError("The active Sketch validity is unavailable.") from exc
    if not valid:
        raise NativeSketchError("The active Sketch is invalid before the operation.")
    return prepared
