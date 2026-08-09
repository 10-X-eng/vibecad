# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact three-point Arc creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCircularArc import (
    circle_point,
    verify_circular_arc_record,
)
from VibeCADNativeSketchCircularThreePoint import circumcircle
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import same_sketch_point, sketch_point_2d
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


_THREE_POINT_ARC_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "first_endpoint_mm",
        "second_endpoint_mm",
        "rim_point_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchThreePointArcSpec:
    target: ActiveSketchTargetSpec
    first_endpoint_mm: tuple[float, float]
    second_endpoint_mm: tuple[float, float]
    rim_point_mm: tuple[float, float]
    center_mm: tuple[float, float]
    radius_mm: float
    first_parameter: float
    last_parameter: float
    rim_parameter: float
    stored_start_mm: tuple[float, float]
    stored_end_mm: tuple[float, float]


@dataclass(frozen=True, slots=True)
class PreparedSketchThreePointArc:
    insertion: PreparedSketchInsertion
    spec: SketchThreePointArcSpec


def _arc_parameters(
    center: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
    rim: tuple[float, float],
) -> tuple[float, float, float, tuple[float, float], tuple[float, float]]:
    def angle(point: tuple[float, float]) -> float:
        return math.atan2(point[1] - center[1], point[0] - center[0]) % math.tau

    first_angle = angle(first)
    second_angle = angle(second)
    rim_angle = angle(rim)
    low = min(first_angle, second_angle)
    high = max(first_angle, second_angle)
    if low < rim_angle < high:
        start, end = low, high
        stored_start, stored_end = (
            (first, second) if first_angle < second_angle else (second, first)
        )
        rim_parameter = rim_angle
    else:
        start, end = high, low + math.tau
        stored_start, stored_end = (
            (first, second) if first_angle > second_angle else (second, first)
        )
        rim_parameter = rim_angle + (math.tau if rim_angle < start else 0.0)
    if not start < rim_parameter < end:
        raise NativeSketchError("Sketch three-point Arc rim ordering is ambiguous.")
    return start, end, rim_parameter, stored_start, stored_end


def prepare_sketch_three_point_arc(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchThreePointArcSpec:
    if not isinstance(value, Mapping) or set(value) != _THREE_POINT_ARC_FIELDS:
        raise NativeSketchError("A Sketch three-point Arc definition has incorrect fields.")
    first = sketch_point_2d(value["first_endpoint_mm"], "three-point Arc first_endpoint_mm")
    second = sketch_point_2d(
        value["second_endpoint_mm"],
        "three-point Arc second_endpoint_mm",
    )
    rim = sketch_point_2d(value["rim_point_mm"], "three-point Arc rim_point_mm")
    center, radius = circumcircle(
        first,
        second,
        rim,
        label="three-point Arc",
        pair_labels=(
            "endpoints",
            "first endpoint and rim point",
            "second endpoint and rim point",
        ),
    )
    start, end, rim_parameter, stored_start, stored_end = _arc_parameters(
        center,
        first,
        second,
        rim,
    )
    return SketchThreePointArcSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        first,
        second,
        rim,
        center,
        radius,
        start,
        end,
        rim_parameter,
        stored_start,
        stored_end,
    )


def preflight_sketch_three_point_arc(
    context: NativeRuntimeContext,
    spec: SketchThreePointArcSpec,
) -> PreparedSketchThreePointArc:
    if not isinstance(spec, SketchThreePointArcSpec):
        raise TypeError("spec must be a SketchThreePointArcSpec")
    return PreparedSketchThreePointArc(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def create_sketch_three_point_arc(
    document: Any,
    prepared: PreparedSketchThreePointArc,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchThreePointArc):
        raise TypeError("prepared must be a PreparedSketchThreePointArc")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after three-point Arc preflight",
    )
    spec = prepared.spec

    import FreeCAD as App
    import Part

    circle = Part.Circle(
        App.Vector(*spec.center_mm, 0.0),
        App.Vector(0.0, 0.0, 1.0),
        spec.radius_mm,
    )
    index = int(
        sketch.addGeometry(
            Part.ArcOfCircle(circle, spec.first_parameter, spec.last_parameter),
            False,
        )
    )
    if index != spec.target.expected_geometry_count:
        raise NativeSketchError(
            "Sketcher returned an unexpected three-point Arc geometry index."
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "geometry_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_three_point_arc(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchThreePointArc = draft.value["prepared"]
    spec = prepared.spec
    sketch, geometry = verify_sketch_insertion(
        document,
        prepared.insertion,
        int(draft.value["geometry_index"]),
    )
    verify_circular_arc_record(
        geometry,
        center_mm=spec.center_mm,
        radius_mm=spec.radius_mm,
        first_parameter=spec.first_parameter,
        last_parameter=spec.last_parameter,
        start_mm=spec.stored_start_mm,
        end_mm=spec.stored_end_mm,
        label="three-point Arc",
    )
    if not same_sketch_point(
        [*circle_point(spec.center_mm, spec.radius_mm, spec.rim_parameter), 0.0],
        spec.rim_point_mm,
    ):
        raise NativeSketchError(
            "Sketch three-point Arc no longer passes through its rim point."
        )
    return sketch_insertion_result(sketch, geometry)
