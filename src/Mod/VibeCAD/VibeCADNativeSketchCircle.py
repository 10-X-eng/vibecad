# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact center-radius Circle creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCircularArc import verify_circle_record
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import sketch_point_2d, sketch_positive_length
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    sketch_insertion_result,
    verify_sketch_insertion,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)
from VibeCADNativeTargets import object_identity


_CIRCLE_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "center_mm",
        "radius_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchCircleSpec:
    target: ActiveSketchTargetSpec
    center_mm: tuple[float, float]
    radius_mm: float


@dataclass(frozen=True, slots=True)
class PreparedSketchCircle:
    insertion: PreparedSketchInsertion
    spec: SketchCircleSpec


def prepare_sketch_circle(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchCircleSpec:
    if not isinstance(value, Mapping) or set(value) != _CIRCLE_FIELDS:
        raise NativeSketchError("A Sketch Circle definition has incorrect fields.")
    return SketchCircleSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        sketch_point_2d(value["center_mm"], "Circle center_mm"),
        sketch_positive_length(value["radius_mm"], "Circle radius_mm"),
    )


def preflight_sketch_circle(
    context: NativeRuntimeContext,
    spec: SketchCircleSpec,
) -> PreparedSketchCircle:
    if not isinstance(spec, SketchCircleSpec):
        raise TypeError("spec must be a SketchCircleSpec")
    return PreparedSketchCircle(preflight_sketch_insertion(context, spec.target), spec)


def create_sketch_circle(
    document: Any,
    prepared: PreparedSketchCircle,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchCircle):
        raise TypeError("prepared must be a PreparedSketchCircle")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Circle preflight",
    )
    spec = prepared.spec
    expected_index = spec.target.expected_geometry_count

    import FreeCAD as App
    import Part

    index = int(
        sketch.addGeometry(
            Part.Circle(
                App.Vector(*spec.center_mm, 0.0),
                App.Vector(0.0, 0.0, 1.0),
                spec.radius_mm,
            ),
            False,
        )
    )
    if index != expected_index:
        raise NativeSketchError("Sketcher returned an unexpected Circle geometry index.")
    return NativeMutationDraft(
        value={"prepared": prepared, "geometry_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_circle(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchCircle = draft.value["prepared"]
    spec = prepared.spec
    sketch, geometry = verify_sketch_insertion(
        document,
        prepared.insertion,
        int(draft.value["geometry_index"]),
    )
    verify_circle_record(
        geometry,
        center_mm=spec.center_mm,
        radius_mm=spec.radius_mm,
        label="Circle",
    )
    return sketch_insertion_result(sketch, geometry)
