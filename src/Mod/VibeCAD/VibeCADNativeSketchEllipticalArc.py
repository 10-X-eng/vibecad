# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact elliptical Arc creation with human-parity internal geometry."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchEllipseCommon import (
    ellipse_axes,
    ellipse_point,
    expected_ellipse_internal_geometry,
    verify_ellipse_record,
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


_ELLIPTICAL_ARC_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "center_mm",
        "major_radius_mm",
        "minor_radius_mm",
        "rotation_degrees",
        "start_parameter_degrees",
        "sweep_parameter_degrees",
    }
)
_INTERNAL_ROLES = (
    "EllipseMajorDiameter",
    "EllipseMinorDiameter",
    "EllipseFocus1",
    "EllipseFocus2",
)


@dataclass(frozen=True, slots=True)
class SketchEllipticalArcSpec:
    target: ActiveSketchTargetSpec
    center_mm: tuple[float, float]
    major_radius_mm: float
    minor_radius_mm: float
    rotation_degrees: float
    start_parameter_degrees: float
    sweep_parameter_degrees: float

    @property
    def first_parameter(self) -> float:
        return math.radians(self.start_parameter_degrees)

    @property
    def last_parameter(self) -> float:
        return math.radians(
            self.start_parameter_degrees + self.sweep_parameter_degrees
        )

    @property
    def major_axis(self) -> tuple[float, float]:
        return ellipse_axes(self.rotation_degrees)[0]

    @property
    def minor_axis(self) -> tuple[float, float]:
        return ellipse_axes(self.rotation_degrees)[1]

    def point_at(self, parameter: float) -> tuple[float, float]:
        return ellipse_point(
            self.center_mm,
            self.major_radius_mm,
            self.minor_radius_mm,
            self.major_axis,
            parameter,
        )


@dataclass(frozen=True, slots=True)
class PreparedSketchEllipticalArc:
    insertion: PreparedSketchInsertion
    spec: SketchEllipticalArcSpec


def prepare_sketch_elliptical_arc(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchEllipticalArcSpec:
    if not isinstance(value, Mapping) or set(value) != _ELLIPTICAL_ARC_FIELDS:
        raise NativeSketchError("A Sketch elliptical Arc definition has incorrect fields.")
    major = sketch_positive_length(
        value["major_radius_mm"],
        "elliptical Arc major_radius_mm",
    )
    minor = sketch_positive_length(
        value["minor_radius_mm"],
        "elliptical Arc minor_radius_mm",
    )
    if minor >= major:
        raise NativeSketchError(
            "Sketch elliptical Arc minor_radius_mm must be smaller than major_radius_mm."
        )
    return SketchEllipticalArcSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        sketch_point_2d(value["center_mm"], "elliptical Arc center_mm"),
        major,
        minor,
        sketch_start_angle_degrees(
            value["rotation_degrees"],
            "elliptical Arc rotation_degrees",
        ),
        sketch_start_angle_degrees(
            value["start_parameter_degrees"],
            "elliptical Arc start_parameter_degrees",
        ),
        sketch_sweep_angle_degrees(
            value["sweep_parameter_degrees"],
            "elliptical Arc sweep_parameter_degrees",
        ),
    )


def preflight_sketch_elliptical_arc(
    context: NativeRuntimeContext,
    spec: SketchEllipticalArcSpec,
) -> PreparedSketchEllipticalArc:
    if not isinstance(spec, SketchEllipticalArcSpec):
        raise TypeError("spec must be a SketchEllipticalArcSpec")
    return PreparedSketchEllipticalArc(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def create_sketch_elliptical_arc(
    document: Any,
    prepared: PreparedSketchEllipticalArc,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchEllipticalArc):
        raise TypeError("prepared must be a PreparedSketchEllipticalArc")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after elliptical Arc preflight",
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
    index = int(
        sketch.addGeometry(
            Part.ArcOfEllipse(
                ellipse,
                spec.first_parameter,
                spec.last_parameter,
            ),
            False,
        )
    )
    if index != source_index:
        raise NativeSketchError(
            "Sketcher returned an unexpected elliptical Arc geometry index."
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


def _verify_main_geometry(
    geometry: Mapping[str, Any],
    spec: SketchEllipticalArcSpec,
) -> None:
    verify_ellipse_record(
        geometry,
        type_id="Part::GeomArcOfEllipse",
        kind="elliptical_arc",
        closed=False,
        center_mm=spec.center_mm,
        major_radius_mm=spec.major_radius_mm,
        minor_radius_mm=spec.minor_radius_mm,
        major_axis=spec.major_axis,
        first_parameter=spec.first_parameter,
        last_parameter=spec.last_parameter,
    )


def verify_sketch_elliptical_arc(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchEllipticalArc = draft.value["prepared"]
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
    _verify_main_geometry(geometry, prepared.spec)
    spec = prepared.spec
    expected_internal = expected_ellipse_internal_geometry(
        spec.center_mm,
        spec.major_radius_mm,
        spec.minor_radius_mm,
        spec.major_axis,
    )
    verify_internal_geometry_records(
        internal_geometries,
        expected_internal,
        label="elliptical Arc",
    )
    verify_internal_alignment_records(
        internal_constraints,
        expected_internal,
        geometry_index=geometry_index,
        internal_indices=internal_indices,
        label="elliptical Arc",
    )
    return sketch_geometry_result(
        sketch,
        {
            "geometry": geometry,
            "internal_geometries": internal_geometries,
            "internal_constraints": internal_constraints,
        },
    )
