# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, non-document-mutating control of circular arc helper visibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records_sha256
from VibeCADNativeSketchPresentationState import (
    FrozenSketchPresentationState,
    freeze_sketch_presentation_state,
    read_arc_overlay_visible,
    write_arc_overlay_visible,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    prepare_active_sketch_target,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import object_reference


@dataclass(frozen=True, slots=True)
class SketchArcOverlaySpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    expected_visible: bool
    visible: bool


@dataclass(frozen=True, slots=True)
class PreparedSketchArcOverlay:
    target: PreparedActiveSketchTarget
    spec: SketchArcOverlaySpec
    state: FrozenSketchPresentationState
    previous_visible: bool


def _count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(
            "Sketch external geometry count must be an integer from 0 to 1000000."
        )
    return value


def prepare_sketch_arc_overlay(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchArcOverlaySpec:
    fields = {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "expected_visible",
        "visible",
    }
    if not isinstance(values, Mapping) or set(values) != fields:
        raise NativeSketchError("Sketch arc overlay has incorrect fields.")
    if (
        type(values["expected_visible"]) is not bool
        or type(values["visible"]) is not bool
    ):
        raise NativeSketchError(
            "Sketch arc overlay visibility values must be booleans."
        )
    return SketchArcOverlaySpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=values["sketch"],
            expected_geometry_count=values["expected_geometry_count"],
            expected_constraint_count=values["expected_constraint_count"],
        ),
        _count(values["expected_external_geometry_count"]),
        values["expected_visible"],
        values["visible"],
    )


def _freeze(sketch: Any, spec: SketchArcOverlaySpec) -> FrozenSketchPresentationState:
    return freeze_sketch_presentation_state(
        sketch,
        expected_geometry_count=spec.target.expected_geometry_count,
        expected_constraint_count=spec.target.expected_constraint_count,
        expected_external_geometry_count=spec.expected_external_geometry_count,
    )


def preflight_sketch_arc_overlay(
    context: NativeRuntimeContext,
    spec: SketchArcOverlaySpec,
) -> PreparedSketchArcOverlay:
    if not isinstance(spec, SketchArcOverlaySpec):
        raise TypeError("spec must be a SketchArcOverlaySpec")
    target = preflight_active_sketch(context, spec.target)
    state = _freeze(target.sketch, spec)
    current = read_arc_overlay_visible()
    if current != spec.expected_visible:
        raise NativeSketchError(
            "Circular arc helper visibility changed; read its current state and retry."
        )
    return PreparedSketchArcOverlay(target, spec, state, current)


def _verify_unchanged(prepared: PreparedSketchArcOverlay) -> Any:
    sketch = require_prepared_active_sketch(
        prepared.target.context.document,
        prepared.target,
    )
    if _freeze(sketch, prepared.spec) != prepared.state:
        raise NativeSketchError(
            "The active Sketch changed during the presentation operation."
        )
    return sketch


def _restore_owned_value(previous: bool, written: bool) -> None:
    if read_arc_overlay_visible() != written:
        return
    write_arc_overlay_visible(previous)
    if read_arc_overlay_visible() != previous:
        raise NativeSketchError(
            "Circular arc helper visibility failed to roll back after "
            "verification failed."
        )


def _result(prepared: PreparedSketchArcOverlay, changed: bool) -> dict[str, Any]:
    state = prepared.state
    return {
        "operation": "arc_overlay",
        "sketch": object_reference(prepared.target.sketch),
        "previous_visible": prepared.previous_visible,
        "visible": prepared.spec.visible,
        "changed": changed,
        "internal_arc_count": state.internal_arc_count,
        "external_arc_count": state.external_arc_count,
        "geometry_count": prepared.spec.target.expected_geometry_count,
        "constraint_count": prepared.spec.target.expected_constraint_count,
        "external_geometry_count": prepared.spec.expected_external_geometry_count,
        "geometry_state_sha256": canonical_sketch_records_sha256(
            state.geometry_records
        ),
        "constraint_state_sha256": canonical_sketch_records_sha256(
            state.constraint_records
        ),
        "external_geometry_state_sha256": canonical_sketch_records_sha256(
            state.external_geometry_records
        ),
    }


def set_sketch_arc_overlay(
    context: NativeRuntimeContext,
    spec: SketchArcOverlaySpec,
) -> dict[str, Any]:
    prepared = preflight_sketch_arc_overlay(context, spec)
    if prepared.previous_visible == spec.visible:
        _verify_unchanged(prepared)
        if read_arc_overlay_visible() != spec.visible:
            raise NativeSketchError(
                "Circular arc helper visibility changed during verification."
            )
        return _result(prepared, False)

    attempted_write = False
    try:
        attempted_write = True
        write_arc_overlay_visible(spec.visible)
        if read_arc_overlay_visible() != spec.visible:
            raise NativeSketchError(
                "Circular arc helper visibility did not reach the requested state."
            )
        _verify_unchanged(prepared)
        if read_arc_overlay_visible() != spec.visible:
            raise NativeSketchError(
                "Circular arc helper visibility changed during verification."
            )
    except Exception:
        if attempted_write:
            _restore_owned_value(prepared.previous_visible, spec.visible)
        raise
    return _result(prepared, True)
