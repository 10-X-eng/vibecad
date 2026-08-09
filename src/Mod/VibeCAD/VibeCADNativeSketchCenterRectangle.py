# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact center-and-corner Rectangle creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    same_sketch_point,
    sketch_coordinate,
    sketch_point_2d,
)
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    sketch_geometry_result,
    verify_sketch_append,
)
from VibeCADNativeSketchRectangleCommon import (
    RectangleBoundary,
    active_rectangle_constraint,
    create_rectangle_boundary,
    exact_rectangle_indices,
    rectangle_boundary,
    verify_rectangle_boundary,
)
from VibeCADNativeSketchState import serialize_sketch_constraint, serialize_sketch_geometry
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
        "corner_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchCenterRectangleSpec:
    target: ActiveSketchTargetSpec
    center_mm: tuple[float, float]
    boundary: RectangleBoundary


@dataclass(frozen=True, slots=True)
class PreparedSketchCenterRectangle:
    insertion: PreparedSketchInsertion
    spec: SketchCenterRectangleSpec


def prepare_sketch_center_rectangle(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchCenterRectangleSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(
            "A Sketch center Rectangle definition has incorrect fields."
        )
    center = sketch_point_2d(value["center_mm"], "center Rectangle center_mm")
    corner = sketch_point_2d(value["corner_mm"], "center Rectangle corner_mm")
    reflected = (
        sketch_coordinate(
            2.0 * center[0] - corner[0],
            "center Rectangle reflected corner x",
        ),
        sketch_coordinate(
            2.0 * center[1] - corner[1],
            "center Rectangle reflected corner y",
        ),
    )
    return SketchCenterRectangleSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        center,
        rectangle_boundary(reflected, corner),
    )


def preflight_sketch_center_rectangle(
    context: NativeRuntimeContext,
    spec: SketchCenterRectangleSpec,
) -> PreparedSketchCenterRectangle:
    if not isinstance(spec, SketchCenterRectangleSpec):
        raise TypeError("spec must be a SketchCenterRectangleSpec")
    return PreparedSketchCenterRectangle(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def create_sketch_center_rectangle(
    document: Any,
    prepared: PreparedSketchCenterRectangle,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchCenterRectangle):
        raise TypeError("prepared must be a PreparedSketchCenterRectangle")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after center Rectangle preflight",
    )
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    geometry_indices, constraint_indices = create_rectangle_boundary(
        sketch,
        spec.boundary,
        base_geometry=base_geometry,
        base_constraint=base_constraint,
    )

    import FreeCAD as App
    import Part
    import Sketcher

    center_index = int(
        sketch.addGeometry(Part.Point(App.Vector(*spec.center_mm, 0.0)), True)
    )
    if center_index != base_geometry + 4:
        raise NativeSketchError(
            "Sketcher returned an unexpected center Rectangle point index."
        )
    symmetry_index = exact_rectangle_indices(
        sketch.addConstraint(
            Sketcher.Constraint(
                "Symmetric",
                geometry_indices[2],
                1,
                geometry_indices[0],
                1,
                center_index,
                1,
            )
        ),
        (base_constraint + 8,),
        "symmetry constraint",
    )[0]
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "geometry_indices": geometry_indices,
            "constraint_indices": constraint_indices,
            "center_index": center_index,
            "symmetry_index": symmetry_index,
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_center_rectangle(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchCenterRectangle = draft.value["prepared"]
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    geometry_indices = tuple(draft.value["geometry_indices"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    center_index = int(draft.value["center_index"])
    symmetry_index = int(draft.value["symmetry_index"])
    if geometry_indices != tuple(range(base_geometry, base_geometry + 4)):
        raise NativeSketchError("Sketch center Rectangle geometry indices changed.")
    if constraint_indices != tuple(range(base_constraint, base_constraint + 8)):
        raise NativeSketchError("Sketch center Rectangle constraint indices changed.")
    if center_index != base_geometry + 4 or symmetry_index != base_constraint + 8:
        raise NativeSketchError("Sketch center Rectangle center linkage indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=5,
        constraints_added=9,
    )
    geometries, constraints = verify_rectangle_boundary(
        sketch,
        spec.boundary,
        geometry_indices,
        constraint_indices,
    )
    center_geometry = serialize_sketch_geometry(sketch, center_index)
    if (
        center_geometry.get("type_id") != "Part::GeomPoint"
        or center_geometry.get("kind") != "point"
        or center_geometry.get("construction") is not True
        or bool(center_geometry.get("blocked"))
        or not same_sketch_point(center_geometry.get("position_mm"), spec.center_mm)
    ):
        raise NativeSketchError("Sketch center Rectangle construction point changed.")
    symmetry = serialize_sketch_constraint(sketch, symmetry_index)
    expected_references = [
        {"slot": 1, "geometry_index": geometry_indices[2], "position": 1},
        {"slot": 2, "geometry_index": geometry_indices[0], "position": 1},
        {"slot": 3, "geometry_index": center_index, "position": 1},
    ]
    if (
        not active_rectangle_constraint(symmetry, "Symmetric")
        or symmetry.get("references") != expected_references
    ):
        raise NativeSketchError("Sketch center Rectangle symmetry constraint changed.")
    return sketch_geometry_result(
        sketch,
        {
            "geometries": geometries,
            "center_geometry": center_geometry,
            "constraints": [*constraints, symmetry],
            "corners_mm": [
                [x, y, 0.0] for x, y in spec.boundary.corners_mm
            ],
            "segment_count": 4,
            "closed": True,
        },
    )
