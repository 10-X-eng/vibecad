# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional Point creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    sketch_insertion_result,
    verify_sketch_insertion,
)
from VibeCADNativeSketchGeometryValues import sketch_point_2d
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)
from VibeCADNativeTargets import object_identity


_POINT_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "position_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchPointSpec:
    target: ActiveSketchTargetSpec
    x_mm: float
    y_mm: float


@dataclass(frozen=True, slots=True)
class PreparedSketchPoint:
    insertion: PreparedSketchInsertion
    x_mm: float
    y_mm: float


def prepare_sketch_point(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchPointSpec:
    if not isinstance(value, Mapping) or set(value) != _POINT_FIELDS:
        raise NativeSketchError("A Sketch Point definition has incorrect fields.")
    position = sketch_point_2d(value["position_mm"], "Point position_mm")
    target = prepare_active_sketch_target(
        document_uid,
        sketch=value["sketch"],
        expected_geometry_count=value["expected_geometry_count"],
        expected_constraint_count=value["expected_constraint_count"],
    )
    return SketchPointSpec(
        target,
        position[0],
        position[1],
    )


def preflight_sketch_point(
    context: NativeRuntimeContext,
    spec: SketchPointSpec,
) -> PreparedSketchPoint:
    if not isinstance(spec, SketchPointSpec):
        raise TypeError("spec must be a SketchPointSpec")
    insertion = preflight_sketch_insertion(context, spec.target)
    return PreparedSketchPoint(
        insertion,
        spec.x_mm,
        spec.y_mm,
    )


def create_sketch_point(
    document: Any,
    prepared: PreparedSketchPoint,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchPoint):
        raise TypeError("prepared must be a PreparedSketchPoint")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Point preflight",
    )
    expected_geometry = prepared.insertion.target.spec.expected_geometry_count

    import FreeCAD as App
    import Part

    index = int(
        sketch.addGeometry(
            Part.Point(App.Vector(prepared.x_mm, prepared.y_mm, 0.0)),
            False,
        )
    )
    if index != expected_geometry:
        raise NativeSketchError("Sketcher returned an unexpected Point geometry index.")
    return NativeMutationDraft(
        value={"prepared": prepared, "geometry_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_point(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchPoint = draft.value["prepared"]
    index = int(draft.value["geometry_index"])
    sketch, geometry = verify_sketch_insertion(
        document,
        prepared.insertion,
        index,
    )
    position = geometry.get("position_mm")
    if (
        geometry.get("type_id") != "Part::GeomPoint"
        or geometry.get("kind") != "point"
        or bool(geometry.get("construction"))
        or bool(geometry.get("blocked"))
        or not isinstance(position, list)
        or len(position) != 3
        or not math.isclose(position[0], prepared.x_mm, rel_tol=0.0, abs_tol=1.0e-9)
        or not math.isclose(position[1], prepared.y_mm, rel_tol=0.0, abs_tol=1.0e-9)
        or not math.isclose(position[2], 0.0, rel_tol=0.0, abs_tol=1.0e-12)
    ):
        raise NativeSketchError("Sketch Point geometry differs from its exact definition.")
    return sketch_insertion_result(sketch, geometry)
