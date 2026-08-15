# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional Line creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    require_distinct_points,
    same_sketch_point,
    sketch_point_2d,
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


_LINE_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "start_mm",
        "end_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchLineSpec:
    target: ActiveSketchTargetSpec
    start_mm: tuple[float, float]
    end_mm: tuple[float, float]


@dataclass(frozen=True, slots=True)
class PreparedSketchLine:
    insertion: PreparedSketchInsertion
    start_mm: tuple[float, float]
    end_mm: tuple[float, float]


def prepare_sketch_line(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchLineSpec:
    if not isinstance(value, Mapping) or set(value) != _LINE_FIELDS:
        raise NativeSketchError("A Sketch Line definition has incorrect fields.")
    start = sketch_point_2d(value["start_mm"], "Line start_mm")
    end = sketch_point_2d(value["end_mm"], "Line end_mm")
    require_distinct_points(start, end, "Line")
    return SketchLineSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        start,
        end,
    )


def preflight_sketch_line(
    context: NativeRuntimeContext,
    spec: SketchLineSpec,
) -> PreparedSketchLine:
    if not isinstance(spec, SketchLineSpec):
        raise TypeError("spec must be a SketchLineSpec")
    return PreparedSketchLine(
        preflight_sketch_insertion(context, spec.target),
        spec.start_mm,
        spec.end_mm,
    )


def create_sketch_line(
    document: Any,
    prepared: PreparedSketchLine,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchLine):
        raise TypeError("prepared must be a PreparedSketchLine")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Line preflight",
    )
    expected_index = prepared.insertion.target.spec.expected_geometry_count

    import FreeCAD as App
    import Part

    index = int(
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(*prepared.start_mm, 0.0),
                App.Vector(*prepared.end_mm, 0.0),
            ),
            False,
        )
    )
    if index != expected_index:
        raise NativeSketchError("Sketcher returned an unexpected Line geometry index.")
    return NativeMutationDraft(
        value={"prepared": prepared, "geometry_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_line(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchLine = draft.value["prepared"]
    sketch, geometry = verify_sketch_insertion(
        document,
        prepared.insertion,
        int(draft.value["geometry_index"]),
    )
    if (
        geometry.get("type_id") != "Part::GeomLineSegment"
        or geometry.get("kind") != "line"
        or bool(geometry.get("construction"))
        or bool(geometry.get("blocked"))
        or not same_sketch_point(geometry.get("start_mm"), prepared.start_mm)
        or not same_sketch_point(geometry.get("end_mm"), prepared.end_mm)
    ):
        raise NativeSketchError("Sketch Line geometry differs from its exact definition.")
    return sketch_insertion_result(sketch, geometry)
