# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact center-radius Arc creation in the human-opened Sketch."""

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
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    sketch_point_2d,
    sketch_positive_length,
    sketch_start_angle_degrees,
    sketch_sweep_angle_degrees,
)
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


_ARC_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "center_mm",
        "radius_mm",
        "start_angle_degrees",
        "sweep_angle_degrees",
    }
)


@dataclass(frozen=True, slots=True)
class SketchArcSpec:
    target: ActiveSketchTargetSpec
    center_mm: tuple[float, float]
    radius_mm: float
    start_angle_degrees: float
    sweep_angle_degrees: float

    @property
    def first_parameter(self) -> float:
        return math.radians(self.start_angle_degrees)

    @property
    def last_parameter(self) -> float:
        return math.radians(self.start_angle_degrees + self.sweep_angle_degrees)

    def point_at(self, parameter: float) -> tuple[float, float]:
        return circle_point(self.center_mm, self.radius_mm, parameter)


@dataclass(frozen=True, slots=True)
class PreparedSketchArc:
    insertion: PreparedSketchInsertion
    spec: SketchArcSpec


def prepare_sketch_arc(document_uid: str, value: Mapping[str, Any]) -> SketchArcSpec:
    if not isinstance(value, Mapping) or set(value) != _ARC_FIELDS:
        raise NativeSketchError("A Sketch Arc definition has incorrect fields.")
    return SketchArcSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        sketch_point_2d(value["center_mm"], "Arc center_mm"),
        sketch_positive_length(value["radius_mm"], "Arc radius_mm"),
        sketch_start_angle_degrees(
            value["start_angle_degrees"],
            "Arc start_angle_degrees",
        ),
        sketch_sweep_angle_degrees(
            value["sweep_angle_degrees"],
            "Arc sweep_angle_degrees",
        ),
    )


def preflight_sketch_arc(
    context: NativeRuntimeContext,
    spec: SketchArcSpec,
) -> PreparedSketchArc:
    if not isinstance(spec, SketchArcSpec):
        raise TypeError("spec must be a SketchArcSpec")
    return PreparedSketchArc(preflight_sketch_insertion(context, spec.target), spec)


def create_sketch_arc(document: Any, prepared: PreparedSketchArc) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchArc):
        raise TypeError("prepared must be a PreparedSketchArc")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Arc preflight",
    )
    spec = prepared.spec
    expected_index = spec.target.expected_geometry_count

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
    if index != expected_index:
        raise NativeSketchError("Sketcher returned an unexpected Arc geometry index.")
    return NativeMutationDraft(
        value={"prepared": prepared, "geometry_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_arc(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchArc = draft.value["prepared"]
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
        start_mm=spec.point_at(spec.first_parameter),
        end_mm=spec.point_at(spec.last_parameter),
        label="Arc",
    )
    return sketch_insertion_result(sketch, geometry)
