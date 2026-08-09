# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact section-view presentation for the human-opened Sketch."""

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


LABEL = "Sketch section view"
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "expected_visible",
        "visible",
    }
)


@dataclass(frozen=True, slots=True)
class SketchSectionViewSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    expected_visible: bool
    visible: bool


@dataclass(frozen=True, slots=True)
class PreparedSketchSectionView:
    target: PreparedActiveSketchTarget
    spec: SketchSectionViewSpec
    state: FrozenSketchPresentationState
    previous_visible: bool


def _count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(
            "Sketch expected_external_geometry_count must be an integer from 0 to "
            "1000000."
        )
    return value


def prepare_sketch_section_view(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchSectionViewSpec:
    if not isinstance(values, Mapping) or set(values) != _FIELDS:
        raise NativeSketchError(f"{LABEL} has incorrect fields.")
    expected = values["expected_visible"]
    visible = values["visible"]
    if type(expected) is not bool or type(visible) is not bool:
        raise NativeSketchError(f"{LABEL} visibility states must be booleans.")
    return SketchSectionViewSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=values["sketch"],
            expected_geometry_count=values["expected_geometry_count"],
            expected_constraint_count=values["expected_constraint_count"],
        ),
        _count(values["expected_external_geometry_count"]),
        expected,
        visible,
    )


def _freeze(
    sketch: Any,
    spec: SketchSectionViewSpec,
) -> FrozenSketchPresentationState:
    return freeze_sketch_presentation_state(
        sketch,
        expected_geometry_count=spec.target.expected_geometry_count,
        expected_constraint_count=spec.target.expected_constraint_count,
        expected_external_geometry_count=spec.expected_external_geometry_count,
    )


def _visible(sketch: Any) -> bool:
    try:
        return bool(sketch.ViewObject.SectionView)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} state is unavailable.") from exc


def preflight_sketch_section_view(
    context: NativeRuntimeContext,
    spec: SketchSectionViewSpec,
) -> PreparedSketchSectionView:
    if not isinstance(spec, SketchSectionViewSpec):
        raise TypeError("spec must be a SketchSectionViewSpec")
    target = preflight_active_sketch(context, spec.target)
    state = _freeze(target.sketch, spec)
    previous = _visible(target.sketch)
    if previous is not spec.expected_visible:
        raise NativeSketchError(f"{LABEL} state changed; read it and retry.")
    return PreparedSketchSectionView(target, spec, state, previous)


def _require_unchanged(prepared: PreparedSketchSectionView) -> Any:
    sketch = require_prepared_active_sketch(
        prepared.target.context.document,
        prepared.target,
    )
    if _freeze(sketch, prepared.spec) != prepared.state:
        raise NativeSketchError(f"The active Sketch changed during {LABEL}.")
    return sketch


def _set_visible(
    context: NativeRuntimeContext,
    prepared: PreparedSketchSectionView,
    visible: bool,
) -> None:
    try:
        import SketcherGui

        result = SketcherGui.setActiveSketchSectionView(
            str(context.document.Name),
            context.document_uid,
            prepared.target.spec.reference.object_name,
            visible,
        )
        if type(result) is not bool or result is not visible:
            raise RuntimeError("Section view returned the wrong state")
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} could not be changed.") from exc


def set_sketch_section_view(
    context: NativeRuntimeContext,
    spec: SketchSectionViewSpec,
) -> dict[str, Any]:
    prepared = preflight_sketch_section_view(context, spec)
    context.guard()
    sketch = _require_unchanged(prepared)
    changed = prepared.previous_visible is not spec.visible
    try:
        if changed:
            _set_visible(context, prepared, spec.visible)
        context.guard()
        _require_unchanged(prepared)
        if _visible(sketch) is not spec.visible:
            raise NativeSketchError(f"{LABEL} did not reach the requested state.")
    except Exception:
        if changed and _visible(sketch) is spec.visible:
            _set_visible(context, prepared, prepared.previous_visible)
        raise
    state = prepared.state
    return {
        "operation": "section_view",
        "sketch": object_reference(sketch),
        "previous_visible": prepared.previous_visible,
        "visible": spec.visible,
        "changed": changed,
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
