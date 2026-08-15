# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact three-point Ellipse creation with human-parity internal geometry."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchEllipseCommon import (
    expected_ellipse_internal_geometry,
    verify_ellipse_record,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MIN_SKETCH_GEOMETRY_LENGTH_MM,
    require_distinct_points,
    sketch_point_2d,
    sketch_positive_length,
)
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    sketch_geometry_result,
    verify_sketch_append,
)
from VibeCADNativeSketchInternalGeometry import (
    exposed_internal_indices,
    verify_internal_alignment_records,
    verify_internal_geometry_records,
)
from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
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
        "first_axis_endpoint_mm",
        "second_axis_endpoint_mm",
        "rim_point_mm",
    }
)
_INTERNAL_ROLES = (
    "EllipseMajorDiameter",
    "EllipseMinorDiameter",
    "EllipseFocus1",
    "EllipseFocus2",
)


@dataclass(frozen=True, slots=True)
class SketchThreePointEllipseSpec:
    target: ActiveSketchTargetSpec
    source_points_mm: tuple[tuple[float, float], ...]
    center_mm: tuple[float, float]
    major_radius_mm: float
    minor_radius_mm: float
    major_axis: tuple[float, float]


@dataclass(frozen=True, slots=True)
class PreparedSketchThreePointEllipse:
    insertion: PreparedSketchInsertion
    spec: SketchThreePointEllipseSpec


def _derived_ellipse(
    first: tuple[float, float],
    second: tuple[float, float],
    rim: tuple[float, float],
) -> tuple[tuple[float, float], float, float, tuple[float, float]]:
    require_distinct_points(first, second, "three-point Ellipse axis")
    center = ((first[0] + second[0]) * 0.5, (first[1] + second[1]) * 0.5)
    axis_delta = (second[0] - center[0], second[1] - center[1])
    first_radius = sketch_positive_length(
        math.hypot(*axis_delta),
        "three-point Ellipse first axis radius",
    )
    first_axis = (axis_delta[0] / first_radius, axis_delta[1] / first_radius)
    transverse_axis = (-first_axis[1], first_axis[0])
    rim_delta = (rim[0] - center[0], rim[1] - center[1])
    axial = rim_delta[0] * first_axis[0] + rim_delta[1] * first_axis[1]
    transverse = (
        rim_delta[0] * transverse_axis[0] + rim_delta[1] * transverse_axis[1]
    )
    remaining = 1.0 - (axial / first_radius) ** 2
    if remaining <= 1.0e-12 or abs(transverse) <= MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError(
            "Sketch three-point Ellipse rim point must lie off the first axis and "
            "project strictly between its endpoints."
        )
    second_radius = sketch_positive_length(
        abs(transverse) / math.sqrt(remaining),
        "three-point Ellipse derived radius",
    )
    if math.isclose(
        first_radius,
        second_radius,
        rel_tol=0.0,
        abs_tol=MIN_SKETCH_GEOMETRY_LENGTH_MM,
    ):
        raise NativeSketchError(
            "Sketch three-point Ellipse points define a Circle, not an Ellipse."
        )
    if second_radius > first_radius:
        direction = 1.0 if transverse > 0.0 else -1.0
        return (
            center,
            second_radius,
            first_radius,
            (direction * transverse_axis[0], direction * transverse_axis[1]),
        )
    return center, first_radius, second_radius, first_axis


def prepare_sketch_three_point_ellipse(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchThreePointEllipseSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(
            "A Sketch three-point Ellipse definition has incorrect fields."
        )
    points = tuple(
        sketch_point_2d(value[key], f"three-point Ellipse {key}")
        for key in (
            "first_axis_endpoint_mm",
            "second_axis_endpoint_mm",
            "rim_point_mm",
        )
    )
    center, major, minor, major_axis = _derived_ellipse(*points)
    return SketchThreePointEllipseSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        points,
        center,
        major,
        minor,
        major_axis,
    )


def preflight_sketch_three_point_ellipse(
    context: NativeRuntimeContext,
    spec: SketchThreePointEllipseSpec,
) -> PreparedSketchThreePointEllipse:
    if not isinstance(spec, SketchThreePointEllipseSpec):
        raise TypeError("spec must be a SketchThreePointEllipseSpec")
    return PreparedSketchThreePointEllipse(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def create_sketch_three_point_ellipse(
    document: Any,
    prepared: PreparedSketchThreePointEllipse,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchThreePointEllipse):
        raise TypeError("prepared must be a PreparedSketchThreePointEllipse")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after three-point Ellipse preflight",
    )
    spec = prepared.spec
    source_index = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part

    ellipse = Part.Ellipse(
        App.Vector(*spec.center_mm, 0.0),
        spec.major_radius_mm,
        spec.minor_radius_mm,
    )
    ellipse.XAxis = App.Vector(*spec.major_axis, 0.0)
    index = int(sketch.addGeometry(ellipse, False))
    if index != source_index:
        raise NativeSketchError(
            "Sketcher returned an unexpected three-point Ellipse geometry index."
        )
    internal_indices = exposed_internal_indices(
        sketch.exposeInternalGeometry(index),
        source_index=index,
        roles=_INTERNAL_ROLES,
        label="ellipse",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "geometry_index": index,
            "internal_indices": internal_indices,
            "constraint_indices": tuple(range(base_constraint, base_constraint + 4)),
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _point_is_on_ellipse(
    point: tuple[float, float],
    spec: SketchThreePointEllipseSpec,
) -> bool:
    delta_x = point[0] - spec.center_mm[0]
    delta_y = point[1] - spec.center_mm[1]
    major_coordinate = delta_x * spec.major_axis[0] + delta_y * spec.major_axis[1]
    minor_axis = (-spec.major_axis[1], spec.major_axis[0])
    minor_coordinate = delta_x * minor_axis[0] + delta_y * minor_axis[1]
    equation = (major_coordinate / spec.major_radius_mm) ** 2 + (
        minor_coordinate / spec.minor_radius_mm
    ) ** 2
    return math.isclose(equation, 1.0, rel_tol=0.0, abs_tol=1.0e-9)


def verify_sketch_three_point_ellipse(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchThreePointEllipse = draft.value["prepared"]
    spec = prepared.spec
    geometry_index = int(draft.value["geometry_index"])
    internal_indices = tuple(draft.value["internal_indices"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=5,
        constraints_added=4,
    )
    geometry = serialize_sketch_geometry(sketch, geometry_index)
    internal_geometries = [
        serialize_sketch_geometry(sketch, index) for index in internal_indices
    ]
    internal_constraints = [
        serialize_sketch_constraint(sketch, index) for index in constraint_indices
    ]
    verify_ellipse_record(
        geometry,
        type_id="Part::GeomEllipse",
        kind="ellipse",
        closed=True,
        center_mm=spec.center_mm,
        major_radius_mm=spec.major_radius_mm,
        minor_radius_mm=spec.minor_radius_mm,
        major_axis=spec.major_axis,
    )
    if not all(_point_is_on_ellipse(point, spec) for point in spec.source_points_mm):
        raise NativeSketchError(
            "Sketch three-point Ellipse no longer passes through all three points."
        )
    expected_internal = expected_ellipse_internal_geometry(
        spec.center_mm,
        spec.major_radius_mm,
        spec.minor_radius_mm,
        spec.major_axis,
    )
    verify_internal_geometry_records(
        internal_geometries,
        expected_internal,
        label="three-point Ellipse",
    )
    verify_internal_alignment_records(
        internal_constraints,
        expected_internal,
        geometry_index=geometry_index,
        internal_indices=internal_indices,
        label="three-point Ellipse",
    )
    return sketch_geometry_result(
        sketch,
        {
            "geometry": geometry,
            "internal_geometries": internal_geometries,
            "internal_constraints": internal_constraints,
        },
    )
