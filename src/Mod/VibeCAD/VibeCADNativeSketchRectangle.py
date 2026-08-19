# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact corner Rectangle creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import sketch_point_2d
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    sketch_constraint_refs,
    sketch_geometry_refs,
    sketch_geometry_result,
    verify_sketch_append,
)
from VibeCADNativeSketchRectangleCommon import (
    RectangleBoundary,
    create_rectangle_boundary,
    rectangle_boundary,
    verify_rectangle_boundary,
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
        "first_corner_mm",
        "opposite_corner_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchRectangleSpec:
    target: ActiveSketchTargetSpec
    boundary: RectangleBoundary


@dataclass(frozen=True, slots=True)
class PreparedSketchRectangle:
    insertion: PreparedSketchInsertion
    spec: SketchRectangleSpec


def prepare_sketch_rectangle(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchRectangleSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError("A Sketch Rectangle definition has incorrect fields.")
    first = sketch_point_2d(value["first_corner_mm"], "Rectangle first_corner_mm")
    opposite = sketch_point_2d(
        value["opposite_corner_mm"],
        "Rectangle opposite_corner_mm",
    )
    return SketchRectangleSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        rectangle_boundary(first, opposite),
    )


def preflight_sketch_rectangle(
    context: NativeRuntimeContext,
    spec: SketchRectangleSpec,
) -> PreparedSketchRectangle:
    if not isinstance(spec, SketchRectangleSpec):
        raise TypeError("spec must be a SketchRectangleSpec")
    return PreparedSketchRectangle(preflight_sketch_insertion(context, spec.target), spec)


def create_sketch_rectangle(
    document: Any,
    prepared: PreparedSketchRectangle,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchRectangle):
        raise TypeError("prepared must be a PreparedSketchRectangle")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Rectangle preflight",
    )
    spec = prepared.spec
    geometry_indices, constraint_indices = create_rectangle_boundary(
        sketch,
        spec.boundary,
        base_geometry=spec.target.expected_geometry_count,
        base_constraint=spec.target.expected_constraint_count,
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "geometry_indices": geometry_indices,
            "constraint_indices": constraint_indices,
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_rectangle(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchRectangle = draft.value["prepared"]
    spec = prepared.spec
    geometry_indices = tuple(draft.value["geometry_indices"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    if geometry_indices != tuple(range(base_geometry, base_geometry + 4)):
        raise NativeSketchError("Sketch Rectangle geometry indices changed.")
    if constraint_indices != tuple(range(base_constraint, base_constraint + 8)):
        raise NativeSketchError("Sketch Rectangle constraint indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=4,
        constraints_added=8,
    )
    geometries, constraints = verify_rectangle_boundary(
        sketch,
        spec.boundary,
        geometry_indices,
        constraint_indices,
    )
    return sketch_geometry_result(
        sketch,
        {
            "geometry_refs": sketch_geometry_refs(geometries),
            "constraint_refs": sketch_constraint_refs(constraints),
            "corners_mm": [
                [x, y, 0.0] for x, y in spec.boundary.corners_mm
            ],
            "segment_count": 4,
            "closed": True,
        },
    )
