# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic connected Polyline creation in the human-opened Sketch."""

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
    sketch_geometry_result,
    verify_sketch_append,
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


MAX_POLYLINE_VERTICES = 65
_POLYLINE_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "vertices_mm",
        "closed",
    }
)


@dataclass(frozen=True, slots=True)
class SketchPolylineSpec:
    target: ActiveSketchTargetSpec
    vertices_mm: tuple[tuple[float, float], ...]
    closed: bool

    @property
    def segments(self) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
        result = tuple(zip(self.vertices_mm, self.vertices_mm[1:]))
        if self.closed:
            result = (*result, (self.vertices_mm[-1], self.vertices_mm[0]))
        return result

    @property
    def joint_count(self) -> int:
        return len(self.segments) if self.closed else len(self.segments) - 1


@dataclass(frozen=True, slots=True)
class PreparedSketchPolyline:
    insertion: PreparedSketchInsertion
    spec: SketchPolylineSpec


def prepare_sketch_polyline(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchPolylineSpec:
    if not isinstance(value, Mapping) or set(value) != _POLYLINE_FIELDS:
        raise NativeSketchError("A Sketch Polyline definition has incorrect fields.")
    raw_vertices = value["vertices_mm"]
    if not isinstance(raw_vertices, list) or not 2 <= len(raw_vertices) <= (
        MAX_POLYLINE_VERTICES
    ):
        raise NativeSketchError(
            f"Sketch Polyline requires 2 to {MAX_POLYLINE_VERTICES} vertices."
        )
    if type(value["closed"]) is not bool:
        raise NativeSketchError("Sketch Polyline closed must be true or false.")
    vertices = tuple(
        sketch_point_2d(item, f"Polyline vertices_mm[{index}]")
        for index, item in enumerate(raw_vertices)
    )
    for index, (start, end) in enumerate(zip(vertices, vertices[1:])):
        require_distinct_points(start, end, f"Polyline segment {index}")
    closed = value["closed"]
    if closed:
        if len(vertices) < 3:
            raise NativeSketchError("A closed Sketch Polyline requires at least 3 vertices.")
        require_distinct_points(vertices[-1], vertices[0], "Polyline closing segment")
    elif len(vertices) > 2:
        try:
            require_distinct_points(vertices[-1], vertices[0], "Polyline")
        except NativeSketchError as exc:
            raise NativeSketchError(
                "A Polyline whose final vertex meets its first must set closed=true."
            ) from exc
    return SketchPolylineSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        vertices,
        closed,
    )


def preflight_sketch_polyline(
    context: NativeRuntimeContext,
    spec: SketchPolylineSpec,
) -> PreparedSketchPolyline:
    if not isinstance(spec, SketchPolylineSpec):
        raise TypeError("spec must be a SketchPolylineSpec")
    return PreparedSketchPolyline(preflight_sketch_insertion(context, spec.target), spec)


def _exact_indices(raw: Any, expected: tuple[int, ...], label: str) -> tuple[int, ...]:
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    result = tuple(int(value) for value in values)
    if result != expected:
        raise NativeSketchError(f"Sketcher returned unexpected Polyline {label} indices.")
    return result


def create_sketch_polyline(
    document: Any,
    prepared: PreparedSketchPolyline,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchPolyline):
        raise TypeError("prepared must be a PreparedSketchPolyline")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Polyline preflight",
    )
    base_geometry = prepared.spec.target.expected_geometry_count
    base_constraint = prepared.spec.target.expected_constraint_count

    import FreeCAD as App
    import Part
    import Sketcher

    lines = [
        Part.LineSegment(App.Vector(*start, 0.0), App.Vector(*end, 0.0))
        for start, end in prepared.spec.segments
    ]
    expected_geometry = tuple(range(base_geometry, base_geometry + len(lines)))
    geometry_indices = _exact_indices(
        sketch.addGeometry(lines, False),
        expected_geometry,
        "geometry",
    )

    joint_pairs = list(zip(geometry_indices, geometry_indices[1:]))
    if prepared.spec.closed:
        joint_pairs.append((geometry_indices[-1], geometry_indices[0]))
    constraints = [
        Sketcher.Constraint("Coincident", first, 2, second, 1)
        for first, second in joint_pairs
    ]
    expected_constraints = tuple(
        range(base_constraint, base_constraint + len(constraints))
    )
    constraint_indices = _exact_indices(
        sketch.addConstraint(constraints) if constraints else (),
        expected_constraints,
        "constraint",
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


def _verify_line_record(
    record: Mapping[str, Any],
    segment: tuple[tuple[float, float], tuple[float, float]],
) -> None:
    if (
        record.get("type_id") != "Part::GeomLineSegment"
        or record.get("kind") != "line"
        or bool(record.get("construction"))
        or bool(record.get("blocked"))
        or not same_sketch_point(record.get("start_mm"), segment[0])
        or not same_sketch_point(record.get("end_mm"), segment[1])
    ):
        raise NativeSketchError("Sketch Polyline segment differs from its definition.")


def verify_sketch_polyline(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchPolyline = draft.value["prepared"]
    spec = prepared.spec
    geometry_indices = tuple(draft.value["geometry_indices"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    expected_geometry = tuple(
        range(base_geometry, base_geometry + len(spec.segments))
    )
    expected_constraints = tuple(
        range(base_constraint, base_constraint + spec.joint_count)
    )
    if geometry_indices != expected_geometry or constraint_indices != expected_constraints:
        raise NativeSketchError("Sketch Polyline output indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=len(geometry_indices),
        constraints_added=len(constraint_indices),
    )

    geometries = [serialize_sketch_geometry(sketch, index) for index in geometry_indices]
    for record, segment in zip(geometries, spec.segments, strict=True):
        _verify_line_record(record, segment)

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in constraint_indices
    ]
    expected_pairs = list(zip(geometry_indices, geometry_indices[1:]))
    if spec.closed:
        expected_pairs.append((geometry_indices[-1], geometry_indices[0]))
    for record, (first, second) in zip(constraints, expected_pairs, strict=True):
        if (
            record.get("type") != "Coincident"
            or record.get("driving") is not True
            or record.get("active") is not True
            or record.get("virtual") is not False
            or record.get("references")
            != [
                {"slot": 1, "geometry_index": first, "position": 2},
                {"slot": 2, "geometry_index": second, "position": 1},
            ]
        ):
            raise NativeSketchError("Sketch Polyline joint constraint changed.")
    return sketch_geometry_result(
        sketch,
        {
            "geometries": geometries,
            "constraints": constraints,
            "segment_count": len(geometries),
            "closed": spec.closed,
        },
    )
