# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parabolic Arc creation with human-parity internal geometry."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MAX_SKETCH_COORDINATE_MM,
    MIN_SKETCH_GEOMETRY_LENGTH_MM,
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


_PARABOLIC_ARC_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "vertex_mm",
        "focal_length_mm",
        "rotation_degrees",
        "start_parameter_mm",
        "end_parameter_mm",
    }
)
_INTERNAL_ROLES = ("ParabolaFocus", "ParabolaFocalAxis")


@dataclass(frozen=True, slots=True)
class SketchParabolicArcSpec:
    target: ActiveSketchTargetSpec
    vertex_mm: tuple[float, float]
    focal_length_mm: float
    rotation_degrees: float
    start_parameter_mm: float
    end_parameter_mm: float

    @property
    def focal_axis(self) -> tuple[float, float]:
        rotation = math.radians(self.rotation_degrees)
        return math.cos(rotation), math.sin(rotation)

    @property
    def transverse_axis(self) -> tuple[float, float]:
        axis_x, axis_y = self.focal_axis
        return -axis_y, axis_x

    @property
    def focus_mm(self) -> tuple[float, float]:
        axis_x, axis_y = self.focal_axis
        return (
            self.vertex_mm[0] + self.focal_length_mm * axis_x,
            self.vertex_mm[1] + self.focal_length_mm * axis_y,
        )

    def point_at(self, parameter_mm: float) -> tuple[float, float]:
        axis_x, axis_y = self.focal_axis
        transverse_x, transverse_y = self.transverse_axis
        axial_distance = (
            parameter_mm * parameter_mm / (4.0 * self.focal_length_mm)
        )
        return (
            self.vertex_mm[0]
            + axial_distance * axis_x
            + parameter_mm * transverse_x,
            self.vertex_mm[1]
            + axial_distance * axis_y
            + parameter_mm * transverse_y,
        )


@dataclass(frozen=True, slots=True)
class PreparedSketchParabolicArc:
    insertion: PreparedSketchInsertion
    spec: SketchParabolicArcSpec


def prepare_sketch_parabolic_arc(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchParabolicArcSpec:
    if not isinstance(value, Mapping) or set(value) != _PARABOLIC_ARC_FIELDS:
        raise NativeSketchError("A Sketch parabolic Arc definition has incorrect fields.")
    start = sketch_bounded_parameter(
        value["start_parameter_mm"],
        "parabolic Arc start_parameter_mm",
        maximum_absolute=MAX_SKETCH_COORDINATE_MM,
    )
    end = sketch_bounded_parameter(
        value["end_parameter_mm"],
        "parabolic Arc end_parameter_mm",
        maximum_absolute=MAX_SKETCH_COORDINATE_MM,
    )
    if end - start <= MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError(
            "Sketch parabolic Arc end_parameter_mm must be greater than "
            "start_parameter_mm by more than one nanometre."
        )
    spec = SketchParabolicArcSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        sketch_point_2d(value["vertex_mm"], "parabolic Arc vertex_mm"),
        sketch_positive_length(
            value["focal_length_mm"],
            "parabolic Arc focal_length_mm",
        ),
        sketch_start_angle_degrees(
            value["rotation_degrees"],
            "parabolic Arc rotation_degrees",
        ),
        start,
        end,
    )
    for label, point in (
        ("focus", spec.focus_mm),
        ("start", spec.point_at(start)),
        ("end", spec.point_at(end)),
    ):
        sketch_coordinate(point[0], f"parabolic Arc {label}.x")
        sketch_coordinate(point[1], f"parabolic Arc {label}.y")
    return spec


def preflight_sketch_parabolic_arc(
    context: NativeRuntimeContext,
    spec: SketchParabolicArcSpec,
) -> PreparedSketchParabolicArc:
    if not isinstance(spec, SketchParabolicArcSpec):
        raise TypeError("spec must be a SketchParabolicArcSpec")
    return PreparedSketchParabolicArc(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def create_sketch_parabolic_arc(
    document: Any,
    prepared: PreparedSketchParabolicArc,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchParabolicArc):
        raise TypeError("prepared must be a PreparedSketchParabolicArc")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after parabolic Arc preflight",
    )
    spec = prepared.spec
    source_index = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part

    parabola = Part.Parabola(
        App.Vector(*spec.focus_mm, 0.0),
        App.Vector(*spec.vertex_mm, 0.0),
        App.Vector(0.0, 0.0, 1.0),
    )
    index = int(
        sketch.addGeometry(
            Part.ArcOfParabola(
                parabola,
                spec.start_parameter_mm,
                spec.end_parameter_mm,
            ),
            False,
        )
    )
    if index != source_index:
        raise NativeSketchError(
            "Sketcher returned an unexpected parabolic Arc geometry index."
        )
    internal_indices = exposed_internal_indices(
        sketch.exposeInternalGeometry(index),
        source_index=index,
        roles=_INTERNAL_ROLES,
        label="parabola",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "geometry_index": index,
            "internal_indices": internal_indices,
            "constraint_indices": tuple(range(base_constraint, base_constraint + 2)),
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _verify_main_geometry(
    geometry: Mapping[str, Any],
    spec: SketchParabolicArcSpec,
) -> None:
    if (
        geometry.get("type_id") != "Part::GeomArcOfParabola"
        or geometry.get("kind") != "parabolic_arc"
        or geometry.get("construction") is not False
        or bool(geometry.get("blocked"))
        or not same_sketch_point(geometry.get("center_mm"), spec.vertex_mm)
        or not same_sketch_vector(geometry.get("axis"), (0.0, 0.0, 1.0))
        or not same_sketch_vector(
            geometry.get("x_axis"),
            (*spec.focal_axis, 0.0),
        )
        or not same_sketch_number(
            geometry.get("focal_length_mm"),
            spec.focal_length_mm,
        )
        or not same_sketch_number(
            geometry.get("first_parameter"),
            spec.start_parameter_mm,
            tolerance=1.0e-10,
        )
        or not same_sketch_number(
            geometry.get("last_parameter"),
            spec.end_parameter_mm,
            tolerance=1.0e-10,
        )
        or not same_sketch_point(
            geometry.get("start_mm"),
            spec.point_at(spec.start_parameter_mm),
        )
        or not same_sketch_point(
            geometry.get("end_mm"),
            spec.point_at(spec.end_parameter_mm),
        )
    ):
        raise NativeSketchError(
            "Sketch parabolic Arc geometry differs from its exact definition."
        )
    if geometry.get("closed") is not False:
        raise NativeSketchError("Sketch parabolic Arc must remain open.")


def _expected_internal_geometry(
    spec: SketchParabolicArcSpec,
) -> tuple[ExpectedInternalGeometry, ...]:
    return (
        ExpectedInternalGeometry(
            "ParabolaFocus",
            "point",
            spec.focus_mm,
        ),
        ExpectedInternalGeometry(
            "ParabolaFocalAxis",
            "line",
            spec.vertex_mm,
            spec.focus_mm,
        ),
    )


def verify_sketch_parabolic_arc(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchParabolicArc = draft.value["prepared"]
    geometry_index = int(draft.value["geometry_index"])
    internal_indices = tuple(draft.value["internal_indices"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=3,
        constraints_added=2,
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
        label="parabolic Arc",
    )
    verify_internal_alignment_records(
        internal_constraints,
        expected_internal,
        geometry_index=geometry_index,
        internal_indices=internal_indices,
        label="parabolic Arc",
    )
    return sketch_geometry_result(
        sketch,
        {
            "geometry": geometry,
            "internal_geometries": internal_geometries,
            "internal_constraints": internal_constraints,
        },
    )
