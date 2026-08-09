# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact camera alignment to the human-opened Sketch plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records_sha256
from VibeCADNativeSketchPresentationState import (
    FrozenSketchPresentationState,
    freeze_sketch_presentation_state,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    prepare_active_sketch_target,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import object_reference


LABEL = "Sketch view alignment"
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
    }
)


@dataclass(frozen=True, slots=True)
class SketchViewAlignmentSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int


@dataclass(frozen=True, slots=True)
class PreparedSketchViewAlignment:
    target: PreparedActiveSketchTarget
    spec: SketchViewAlignmentSpec
    state: FrozenSketchPresentationState


def _count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(
            "Sketch expected_external_geometry_count must be an integer from 0 to "
            "1000000."
        )
    return value


def prepare_sketch_view_alignment(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchViewAlignmentSpec:
    if not isinstance(values, Mapping) or set(values) != _FIELDS:
        raise NativeSketchError(f"{LABEL} has incorrect fields.")
    return SketchViewAlignmentSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=values["sketch"],
            expected_geometry_count=values["expected_geometry_count"],
            expected_constraint_count=values["expected_constraint_count"],
        ),
        _count(values["expected_external_geometry_count"]),
    )


def _freeze(
    sketch: Any,
    spec: SketchViewAlignmentSpec,
) -> FrozenSketchPresentationState:
    return freeze_sketch_presentation_state(
        sketch,
        expected_geometry_count=spec.target.expected_geometry_count,
        expected_constraint_count=spec.target.expected_constraint_count,
        expected_external_geometry_count=spec.expected_external_geometry_count,
    )


def preflight_sketch_view_alignment(
    context: NativeRuntimeContext,
    spec: SketchViewAlignmentSpec,
) -> PreparedSketchViewAlignment:
    if not isinstance(spec, SketchViewAlignmentSpec):
        raise TypeError("spec must be a SketchViewAlignmentSpec")
    target = preflight_active_sketch(context, spec.target)
    return PreparedSketchViewAlignment(target, spec, _freeze(target.sketch, spec))


def _require_unchanged(prepared: PreparedSketchViewAlignment) -> Any:
    sketch = require_prepared_active_sketch(
        prepared.target.context.document,
        prepared.target,
    )
    if _freeze(sketch, prepared.spec) != prepared.state:
        raise NativeSketchError(f"The active Sketch changed during {LABEL}.")
    return sketch


def _view_rotations() -> tuple[Any, Any]:
    try:
        import FreeCAD as App
        import FreeCADGui as Gui

        gui_document = Gui.activeDocument()
        if gui_document is None:
            raise RuntimeError("No active GUI document")
        view = gui_document.activeView()
        current = view.getCameraOrientation()
        target = App.Placement(gui_document.EditingTransform).Rotation
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} camera state is unavailable.") from exc
    return current, target


def _set_camera_orientation(rotation: Any) -> None:
    try:
        import FreeCADGui as Gui

        view = Gui.activeDocument().activeView()
        animation_enabled = bool(view.isAnimationEnabled())
        if animation_enabled:
            view.setAnimationEnabled(False)
        try:
            view.setCameraOrientation(rotation)
        finally:
            if animation_enabled:
                view.setAnimationEnabled(True)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} camera state could not be changed.") from exc


def align_view_to_sketch(
    context: NativeRuntimeContext,
    spec: SketchViewAlignmentSpec,
) -> dict[str, Any]:
    prepared = preflight_sketch_view_alignment(context, spec)
    context.guard()
    sketch = _require_unchanged(prepared)
    previous, target = _view_rotations()
    changed = not bool(previous.isSame(target, 1.0e-10))
    try:
        if changed:
            _set_camera_orientation(target)
        context.guard()
        _require_unchanged(prepared)
        current, expected = _view_rotations()
        if not bool(current.isSame(expected, 1.0e-9)):
            raise NativeSketchError(f"{LABEL} did not reach the requested orientation.")
    except Exception:
        if changed:
            try:
                _set_camera_orientation(previous)
            except Exception:
                pass
        raise
    state = prepared.state
    return {
        "operation": "align_view_to_sketch",
        "sketch": object_reference(sketch),
        "changed": changed,
        "camera_orientation_xyzw": [float(value) for value in target.Q],
        "geometry_count": spec.target.expected_geometry_count,
        "constraint_count": spec.target.expected_constraint_count,
        "external_geometry_count": spec.expected_external_geometry_count,
        "geometry_state_sha256": canonical_sketch_records_sha256(
            state.geometry_records
        ),
        "constraint_state_sha256": canonical_sketch_records_sha256(
            state.constraint_records
        ),
    }
