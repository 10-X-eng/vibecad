# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact three-point Circle creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCircularArc import verify_circle_record
from VibeCADNativeSketchCircularThreePoint import circumcircle
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import same_sketch_number, sketch_point_2d
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


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "first_point_mm",
        "second_point_mm",
        "third_point_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchThreePointCircleSpec:
    target: ActiveSketchTargetSpec
    points_mm: tuple[tuple[float, float], ...]
    center_mm: tuple[float, float]
    radius_mm: float


@dataclass(frozen=True, slots=True)
class PreparedSketchThreePointCircle:
    insertion: PreparedSketchInsertion
    spec: SketchThreePointCircleSpec


def prepare_sketch_three_point_circle(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchThreePointCircleSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(
            "A Sketch three-point Circle definition has incorrect fields."
        )
    points = tuple(
        sketch_point_2d(value[key], f"three-point Circle {key}")
        for key in ("first_point_mm", "second_point_mm", "third_point_mm")
    )
    center, radius = circumcircle(*points, label="three-point Circle")
    return SketchThreePointCircleSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        points,
        center,
        radius,
    )


def preflight_sketch_three_point_circle(
    context: NativeRuntimeContext,
    spec: SketchThreePointCircleSpec,
) -> PreparedSketchThreePointCircle:
    if not isinstance(spec, SketchThreePointCircleSpec):
        raise TypeError("spec must be a SketchThreePointCircleSpec")
    return PreparedSketchThreePointCircle(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def create_sketch_three_point_circle(
    document: Any,
    prepared: PreparedSketchThreePointCircle,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchThreePointCircle):
        raise TypeError("prepared must be a PreparedSketchThreePointCircle")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after three-point Circle preflight",
    )
    spec = prepared.spec

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
    if index != spec.target.expected_geometry_count:
        raise NativeSketchError(
            "Sketcher returned an unexpected three-point Circle geometry index."
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "geometry_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_three_point_circle(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchThreePointCircle = draft.value["prepared"]
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
        label="three-point Circle",
    )
    if not all(
        same_sketch_number(
            math.hypot(point[0] - spec.center_mm[0], point[1] - spec.center_mm[1]),
            spec.radius_mm,
        )
        for point in spec.points_mm
    ):
        raise NativeSketchError(
            "Sketch three-point Circle no longer passes through all three points."
        )
    return sketch_insertion_result(sketch, geometry)
