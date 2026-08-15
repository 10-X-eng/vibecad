# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact hyperbolic Arc creation with human-parity internal geometry."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    same_sketch_number,
    same_sketch_point,
    same_sketch_vector,
    sketch_bounded_parameter,
    sketch_coordinate,
    sketch_point_2d,
    sketch_positive_length,
    sketch_start_angle_degrees,
)
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    sketch_geometry_result,
    verify_sketch_append,
)
from VibeCADNativeSketchInternalGeometry import (
    ExpectedInternalGeometry,
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


MAX_HYPERBOLA_PARAMETER = 20.0
_HYPERBOLIC_ARC_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "center_mm",
        "major_radius_mm",
        "minor_radius_mm",
        "rotation_degrees",
        "start_parameter",
        "end_parameter",
    }
)
_INTERNAL_ROLES = ("HyperbolaMajor", "HyperbolaMinor", "HyperbolaFocus")


@dataclass(frozen=True, slots=True)
class SketchHyperbolicArcSpec:
    target: ActiveSketchTargetSpec
    center_mm: tuple[float, float]
    major_radius_mm: float
    minor_radius_mm: float
    rotation_degrees: float
    start_parameter: float
    end_parameter: float

    @property
    def major_axis(self) -> tuple[float, float]:
        rotation = math.radians(self.rotation_degrees)
        return math.cos(rotation), math.sin(rotation)

    @property
    def minor_axis(self) -> tuple[float, float]:
        major_x, major_y = self.major_axis
        return -major_y, major_x

    def point_at(self, parameter: float) -> tuple[float, float]:
        major_x, major_y = self.major_axis
        minor_x, minor_y = self.minor_axis
        return (
            self.center_mm[0]
            + self.major_radius_mm * math.cosh(parameter) * major_x
            + self.minor_radius_mm * math.sinh(parameter) * minor_x,
            self.center_mm[1]
            + self.major_radius_mm * math.cosh(parameter) * major_y
            + self.minor_radius_mm * math.sinh(parameter) * minor_y,
        )


@dataclass(frozen=True, slots=True)
class PreparedSketchHyperbolicArc:
    insertion: PreparedSketchInsertion
    spec: SketchHyperbolicArcSpec


def prepare_sketch_hyperbolic_arc(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchHyperbolicArcSpec:
    if not isinstance(value, Mapping) or set(value) != _HYPERBOLIC_ARC_FIELDS:
        raise NativeSketchError("A Sketch hyperbolic Arc definition has incorrect fields.")
    start = sketch_bounded_parameter(
        value["start_parameter"],
        "hyperbolic Arc start_parameter",
        maximum_absolute=MAX_HYPERBOLA_PARAMETER,
    )
    end = sketch_bounded_parameter(
        value["end_parameter"],
        "hyperbolic Arc end_parameter",
        maximum_absolute=MAX_HYPERBOLA_PARAMETER,
    )
    if end - start <= 1.0e-12:
        raise NativeSketchError(
            "Sketch hyperbolic Arc end_parameter must be greater than start_parameter."
        )
    spec = SketchHyperbolicArcSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        sketch_point_2d(value["center_mm"], "hyperbolic Arc center_mm"),
        sketch_positive_length(
            value["major_radius_mm"],
            "hyperbolic Arc major_radius_mm",
        ),
        sketch_positive_length(
            value["minor_radius_mm"],
            "hyperbolic Arc minor_radius_mm",
        ),
        sketch_start_angle_degrees(
            value["rotation_degrees"],
            "hyperbolic Arc rotation_degrees",
        ),
        start,
        end,
    )
    for label, point in (
        ("start", spec.point_at(start)),
        ("end", spec.point_at(end)),
    ):
        sketch_coordinate(point[0], f"hyperbolic Arc {label}.x")
        sketch_coordinate(point[1], f"hyperbolic Arc {label}.y")
    return spec


def preflight_sketch_hyperbolic_arc(
    context: NativeRuntimeContext,
    spec: SketchHyperbolicArcSpec,
) -> PreparedSketchHyperbolicArc:
    if not isinstance(spec, SketchHyperbolicArcSpec):
        raise TypeError("spec must be a SketchHyperbolicArcSpec")
    return PreparedSketchHyperbolicArc(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def create_sketch_hyperbolic_arc(
    document: Any,
    prepared: PreparedSketchHyperbolicArc,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchHyperbolicArc):
        raise TypeError("prepared must be a PreparedSketchHyperbolicArc")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after hyperbolic Arc preflight",
    )
    spec = prepared.spec
    source_index = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part

    hyperbola = Part.Hyperbola(
        App.Vector(*spec.center_mm, 0.0),
        spec.major_radius_mm,
        spec.minor_radius_mm,
    )
    hyperbola.XAxis = App.Vector(*spec.major_axis, 0.0)
    index = int(
        sketch.addGeometry(
            Part.ArcOfHyperbola(
                hyperbola,
                spec.start_parameter,
                spec.end_parameter,
            ),
            False,
        )
    )
    if index != source_index:
        raise NativeSketchError(
            "Sketcher returned an unexpected hyperbolic Arc geometry index."
        )
    internal_indices = exposed_internal_indices(
        sketch.exposeInternalGeometry(index),
        source_index=index,
        roles=_INTERNAL_ROLES,
        label="hyperbola",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "geometry_index": index,
            "internal_indices": internal_indices,
            "constraint_indices": tuple(range(base_constraint, base_constraint + 3)),
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _verify_main_geometry(
    geometry: Mapping[str, Any],
    spec: SketchHyperbolicArcSpec,
) -> None:
    if (
        geometry.get("type_id") != "Part::GeomArcOfHyperbola"
        or geometry.get("kind") != "hyperbolic_arc"
        or bool(geometry.get("construction"))
        or bool(geometry.get("blocked"))
        or geometry.get("closed") is not False
        or not same_sketch_point(geometry.get("center_mm"), spec.center_mm)
        or not same_sketch_vector(geometry.get("axis"), (0.0, 0.0, 1.0))
        or not same_sketch_vector(
            geometry.get("x_axis"),
            (*spec.major_axis, 0.0),
        )
        or not same_sketch_number(
            geometry.get("major_radius_mm"),
            spec.major_radius_mm,
        )
        or not same_sketch_number(
            geometry.get("minor_radius_mm"),
            spec.minor_radius_mm,
        )
        or not same_sketch_number(
            geometry.get("first_parameter"),
            spec.start_parameter,
            tolerance=1.0e-10,
        )
        or not same_sketch_number(
            geometry.get("last_parameter"),
            spec.end_parameter,
            tolerance=1.0e-10,
        )
        or not same_sketch_point(
            geometry.get("start_mm"),
            spec.point_at(spec.start_parameter),
        )
        or not same_sketch_point(
            geometry.get("end_mm"),
            spec.point_at(spec.end_parameter),
        )
    ):
        raise NativeSketchError(
            "Sketch hyperbolic Arc geometry differs from its exact definition."
        )


def _expected_internal_geometry(
    spec: SketchHyperbolicArcSpec,
) -> tuple[ExpectedInternalGeometry, ...]:
    center_x, center_y = spec.center_mm
    major_x, major_y = spec.major_axis
    minor_x, minor_y = spec.minor_axis
    positive_major = (
        center_x + spec.major_radius_mm * major_x,
        center_y + spec.major_radius_mm * major_y,
    )
    negative_major = (
        center_x - spec.major_radius_mm * major_x,
        center_y - spec.major_radius_mm * major_y,
    )
    focus = math.sqrt(
        spec.major_radius_mm * spec.major_radius_mm
        + spec.minor_radius_mm * spec.minor_radius_mm
    )
    return (
        ExpectedInternalGeometry(
            "HyperbolaMajor",
            "line",
            positive_major,
            negative_major,
        ),
        ExpectedInternalGeometry(
            "HyperbolaMinor",
            "line",
            (
                positive_major[0] + spec.minor_radius_mm * minor_x,
                positive_major[1] + spec.minor_radius_mm * minor_y,
            ),
            (
                positive_major[0] - spec.minor_radius_mm * minor_x,
                positive_major[1] - spec.minor_radius_mm * minor_y,
            ),
        ),
        ExpectedInternalGeometry(
            "HyperbolaFocus",
            "point",
            (center_x + focus * major_x, center_y + focus * major_y),
        ),
    )


def verify_sketch_hyperbolic_arc(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchHyperbolicArc = draft.value["prepared"]
    geometry_index = int(draft.value["geometry_index"])
    internal_indices = tuple(draft.value["internal_indices"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=4,
        constraints_added=3,
    )
    geometry = serialize_sketch_geometry(sketch, geometry_index)
    internal_geometries = [
        serialize_sketch_geometry(sketch, index) for index in internal_indices
    ]
    internal_constraints = [
        serialize_sketch_constraint(sketch, index) for index in constraint_indices
    ]
    _verify_main_geometry(geometry, prepared.spec)
    expected_internal = _expected_internal_geometry(prepared.spec)
    verify_internal_geometry_records(
        internal_geometries,
        expected_internal,
        label="hyperbolic Arc",
    )
    verify_internal_alignment_records(
        internal_constraints,
        expected_internal,
        geometry_index=geometry_index,
        internal_indices=internal_indices,
        label="hyperbolic Arc",
    )
    return sketch_geometry_result(
        sketch,
        {
            "geometry": geometry,
            "internal_geometries": internal_geometries,
            "internal_constraints": internal_constraints,
        },
    )
