# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact center-based Ellipse creation with human-parity internal geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchEllipseCommon import (
    ellipse_axes,
    expected_ellipse_internal_geometry,
    verify_ellipse_record,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
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
        "center_mm",
        "major_radius_mm",
        "minor_radius_mm",
        "rotation_degrees",
    }
)
_INTERNAL_ROLES = (
    "EllipseMajorDiameter",
    "EllipseMinorDiameter",
    "EllipseFocus1",
    "EllipseFocus2",
)


@dataclass(frozen=True, slots=True)
class SketchEllipseSpec:
    target: ActiveSketchTargetSpec
    center_mm: tuple[float, float]
    major_radius_mm: float
    minor_radius_mm: float
    rotation_degrees: float

    @property
    def major_axis(self) -> tuple[float, float]:
        return ellipse_axes(self.rotation_degrees)[0]


@dataclass(frozen=True, slots=True)
class PreparedSketchEllipse:
    insertion: PreparedSketchInsertion
    spec: SketchEllipseSpec


def prepare_sketch_ellipse(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchEllipseSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError("A Sketch Ellipse definition has incorrect fields.")
    major = sketch_positive_length(value["major_radius_mm"], "Ellipse major_radius_mm")
    minor = sketch_positive_length(value["minor_radius_mm"], "Ellipse minor_radius_mm")
    if minor >= major:
        raise NativeSketchError(
            "Sketch Ellipse minor_radius_mm must be smaller than major_radius_mm."
        )
    return SketchEllipseSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        sketch_point_2d(value["center_mm"], "Ellipse center_mm"),
        major,
        minor,
        sketch_start_angle_degrees(value["rotation_degrees"], "Ellipse rotation_degrees"),
    )


def preflight_sketch_ellipse(
    context: NativeRuntimeContext,
    spec: SketchEllipseSpec,
) -> PreparedSketchEllipse:
    if not isinstance(spec, SketchEllipseSpec):
        raise TypeError("spec must be a SketchEllipseSpec")
    return PreparedSketchEllipse(preflight_sketch_insertion(context, spec.target), spec)


def create_sketch_ellipse(
    document: Any,
    prepared: PreparedSketchEllipse,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchEllipse):
        raise TypeError("prepared must be a PreparedSketchEllipse")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Ellipse preflight",
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
        raise NativeSketchError("Sketcher returned an unexpected Ellipse geometry index.")
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


def verify_sketch_ellipse(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchEllipse = draft.value["prepared"]
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
    expected_internal = expected_ellipse_internal_geometry(
        spec.center_mm,
        spec.major_radius_mm,
        spec.minor_radius_mm,
        spec.major_axis,
    )
    verify_internal_geometry_records(
        internal_geometries,
        expected_internal,
        label="Ellipse",
    )
    verify_internal_alignment_records(
        internal_constraints,
        expected_internal,
        geometry_index=geometry_index,
        internal_indices=internal_indices,
        label="Ellipse",
    )
    return sketch_geometry_result(
        sketch,
        {
            "geometry": geometry,
            "internal_geometries": internal_geometries,
            "internal_constraints": internal_constraints,
        },
    )
