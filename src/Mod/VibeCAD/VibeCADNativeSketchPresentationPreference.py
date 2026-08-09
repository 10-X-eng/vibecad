# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared stale-safe preference engine for exact Sketch presentation state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchPresentationState import (
    FrozenSketchPresentationState,
    freeze_sketch_presentation_state,
    read_sketch_presentation_visible,
    write_sketch_presentation_visible,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    prepare_active_sketch_target,
    require_prepared_active_sketch,
)


PRESENTATION_REQUEST_FIELDS = frozenset(
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
class SketchPresentationPreference:
    operation: str
    key: str
    default_visible: bool
    label: str


@dataclass(frozen=True, slots=True)
class SketchPresentationPreferenceSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    expected_visible: bool
    visible: bool
    preference: SketchPresentationPreference


@dataclass(frozen=True, slots=True)
class PreparedSketchPresentationPreference:
    target: PreparedActiveSketchTarget
    spec: SketchPresentationPreferenceSpec
    state: FrozenSketchPresentationState
    previous_visible: bool


@dataclass(frozen=True, slots=True)
class AppliedSketchPresentationPreference:
    prepared: PreparedSketchPresentationPreference
    changed: bool


def _count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(
            "Sketch external geometry count must be an integer from 0 to 1000000."
        )
    return value


def prepare_sketch_presentation_preference(
    document_uid: str,
    values: Mapping[str, Any],
    preference: SketchPresentationPreference,
) -> SketchPresentationPreferenceSpec:
    if not isinstance(preference, SketchPresentationPreference):
        raise TypeError("preference must be a SketchPresentationPreference")
    if not isinstance(values, Mapping) or set(values) != PRESENTATION_REQUEST_FIELDS:
        raise NativeSketchError(f"Sketch {preference.operation} has incorrect fields.")
    if (
        type(values["expected_visible"]) is not bool
        or type(values["visible"]) is not bool
    ):
        raise NativeSketchError(
            f"Sketch {preference.operation} visibility values must be booleans."
        )
    return SketchPresentationPreferenceSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=values["sketch"],
            expected_geometry_count=values["expected_geometry_count"],
            expected_constraint_count=values["expected_constraint_count"],
        ),
        _count(values["expected_external_geometry_count"]),
        values["expected_visible"],
        values["visible"],
        preference,
    )


def _freeze(
    sketch: Any,
    spec: SketchPresentationPreferenceSpec,
) -> FrozenSketchPresentationState:
    return freeze_sketch_presentation_state(
        sketch,
        expected_geometry_count=spec.target.expected_geometry_count,
        expected_constraint_count=spec.target.expected_constraint_count,
        expected_external_geometry_count=spec.expected_external_geometry_count,
    )


def _read(spec: SketchPresentationPreferenceSpec) -> bool:
    preference = spec.preference
    return read_sketch_presentation_visible(
        preference.key,
        preference.default_visible,
        preference.label,
    )


def _write(spec: SketchPresentationPreferenceSpec, visible: bool) -> None:
    preference = spec.preference
    write_sketch_presentation_visible(preference.key, visible, preference.label)


def preflight_sketch_presentation_preference(
    context: NativeRuntimeContext,
    spec: SketchPresentationPreferenceSpec,
) -> PreparedSketchPresentationPreference:
    if not isinstance(spec, SketchPresentationPreferenceSpec):
        raise TypeError("spec must be a SketchPresentationPreferenceSpec")
    target = preflight_active_sketch(context, spec.target)
    state = _freeze(target.sketch, spec)
    current = _read(spec)
    if current != spec.expected_visible:
        raise NativeSketchError(
            f"{spec.preference.label} changed; read its current state and retry."
        )
    return PreparedSketchPresentationPreference(target, spec, state, current)


def _verify_unchanged(prepared: PreparedSketchPresentationPreference) -> None:
    sketch = require_prepared_active_sketch(
        prepared.target.context.document,
        prepared.target,
    )
    if _freeze(sketch, prepared.spec) != prepared.state:
        raise NativeSketchError(
            "The active Sketch changed during the presentation operation."
        )


def _restore_owned_value(
    spec: SketchPresentationPreferenceSpec,
    previous: bool,
) -> None:
    if _read(spec) != spec.visible:
        return
    _write(spec, previous)
    if _read(spec) != previous:
        raise NativeSketchError(
            f"{spec.preference.label} failed to roll back after verification failed."
        )


def apply_sketch_presentation_preference(
    context: NativeRuntimeContext,
    spec: SketchPresentationPreferenceSpec,
) -> AppliedSketchPresentationPreference:
    prepared = preflight_sketch_presentation_preference(context, spec)
    if prepared.previous_visible == spec.visible:
        _verify_unchanged(prepared)
        if _read(spec) != spec.visible:
            raise NativeSketchError(
                f"{spec.preference.label} changed during verification."
            )
        return AppliedSketchPresentationPreference(prepared, False)

    attempted_write = False
    try:
        attempted_write = True
        _write(spec, spec.visible)
        if _read(spec) != spec.visible:
            raise NativeSketchError(
                f"{spec.preference.label} did not reach the requested state."
            )
        _verify_unchanged(prepared)
        if _read(spec) != spec.visible:
            raise NativeSketchError(
                f"{spec.preference.label} changed during verification."
            )
    except Exception:
        if attempted_write:
            _restore_owned_value(spec, prepared.previous_visible)
        raise
    return AppliedSketchPresentationPreference(prepared, True)
