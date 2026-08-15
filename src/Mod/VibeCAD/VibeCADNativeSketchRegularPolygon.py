# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact human-command implementation for regular Sketch polygons."""

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


REGULAR_POLYGON_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "center_mm",
        "corner_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchRegularPolygonSpec:
    target: ActiveSketchTargetSpec
    label: str
    side_count: int
    center_mm: tuple[float, float]
    corner_mm: tuple[float, float]
    radius_mm: float
    vertices_mm: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class PreparedSketchRegularPolygon:
    insertion: PreparedSketchInsertion
    spec: SketchRegularPolygonSpec


def prepare_sketch_regular_polygon(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    side_count: int,
    label: str,
) -> SketchRegularPolygonSpec:
    if type(side_count) is not int or not 3 <= side_count <= 9_999:
        raise ValueError("side_count must be an integer from 3 through 9999")
    if not isinstance(label, str) or not label:
        raise ValueError("label must be a non-empty string")
    if not isinstance(value, Mapping) or set(value) != REGULAR_POLYGON_FIELDS:
        raise NativeSketchError(
            f"A Sketch {label} definition has incorrect fields."
        )
    center = sketch_point_2d(value["center_mm"], f"{label} center_mm")
    corner = sketch_point_2d(value["corner_mm"], f"{label} corner_mm")
    delta_x = corner[0] - center[0]
    delta_y = corner[1] - center[1]
    radius = math.hypot(delta_x, delta_y)
    if radius <= MIN_SKETCH_GEOMETRY_LENGTH_MM:
        raise NativeSketchError(
            f"Sketch {label} center_mm and corner_mm must be distinct."
        )
    if radius > MAX_SKETCH_COORDINATE_MM:
        raise NativeSketchError(
            f"Sketch {label} radius must not exceed "
            f"{int(MAX_SKETCH_COORDINATE_MM)} mm."
        )
    separation = math.tau / side_count
    vertices = tuple(
        (
            center[0]
            + math.cos(separation * index) * delta_x
            - math.sin(separation * index) * delta_y,
            center[1]
            + math.cos(separation * index) * delta_y
            + math.sin(separation * index) * delta_x,
        )
        for index in range(side_count)
    )
    if any(
        abs(coordinate) > MAX_SKETCH_COORDINATE_MM
        for point in vertices
        for coordinate in point
    ):
        raise NativeSketchError(
            f"Sketch {label} vertices must remain within "
            f"+/-{int(MAX_SKETCH_COORDINATE_MM)} mm."
        )
    return SketchRegularPolygonSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        label,
        side_count,
        center,
        corner,
        radius,
        vertices,
    )


def preflight_sketch_regular_polygon(
    context: NativeRuntimeContext,
    spec: SketchRegularPolygonSpec,
) -> PreparedSketchRegularPolygon:
    if not isinstance(spec, SketchRegularPolygonSpec):
        raise TypeError("spec must be a SketchRegularPolygonSpec")
    return PreparedSketchRegularPolygon(
        preflight_sketch_insertion(context, spec.target),
        spec,
    )


def _exact_indices(raw: Any, expected: tuple[int, ...], label: str) -> tuple[int, ...]:
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    result = tuple(int(value) for value in values)
    if result != expected:
        raise NativeSketchError(f"Sketcher returned unexpected {label} indices.")
    return result


def create_sketch_regular_polygon(
    document: Any,
    prepared: PreparedSketchRegularPolygon,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchRegularPolygon):
        raise TypeError("prepared must be a PreparedSketchRegularPolygon")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage=f"after {prepared.spec.label} preflight",
    )
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part
    import Sketcher

    geometries = [
        Part.LineSegment(
            App.Vector(*spec.vertices_mm[index], 0.0),
            App.Vector(*spec.vertices_mm[(index + 1) % spec.side_count], 0.0),
        )
        for index in range(spec.side_count)
    ]
    geometries.append(
        Part.Circle(
            App.Vector(*spec.center_mm, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            spec.radius_mm,
        )
    )
    geometry_indices = _exact_indices(
        sketch.addGeometry(geometries, False),
        tuple(range(base_geometry, base_geometry + spec.side_count + 1)),
        f"{spec.label} geometry",
    )
    side_indices = geometry_indices[:-1]
    circle_index = geometry_indices[-1]
    sketch.setConstruction(circle_index, True)

    constraints = [
        Sketcher.Constraint(
            "Coincident",
            side_indices[index],
            2,
            side_indices[(index + 1) % spec.side_count],
            1,
        )
        for index in range(spec.side_count)
    ]
    constraints.extend(
        Sketcher.Constraint("Equal", side_indices[0], side_index)
        for side_index in side_indices[1:]
    )
    constraints.extend(
        Sketcher.Constraint("PointOnObject", side_index, 2, circle_index)
        for side_index in side_indices
    )
    constraint_count = spec.side_count * 3 - 1
    constraint_indices = _exact_indices(
        sketch.addConstraint(constraints),
        tuple(range(base_constraint, base_constraint + constraint_count)),
        f"{spec.label} constraint",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "side_indices": side_indices,
            "circle_index": circle_index,
            "constraint_indices": constraint_indices,
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _active_constraint(
    record: Mapping[str, Any],
    constraint_type: str,
    references: list[dict[str, int]],
    label: str,
) -> None:
    if (
        record.get("type") != constraint_type
        or record.get("driving") is not True
        or record.get("active") is not True
        or record.get("virtual") is not False
        or record.get("references") != references
    ):
        raise NativeSketchError(f"Sketch {label} constraint changed.")


def verify_sketch_regular_polygon(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchRegularPolygon = draft.value["prepared"]
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    sides = tuple(draft.value["side_indices"])
    circle = int(draft.value["circle_index"])
    constraint_indices = tuple(draft.value["constraint_indices"])
    if sides != tuple(range(base_geometry, base_geometry + spec.side_count)):
        raise NativeSketchError(f"Sketch {spec.label} side indices changed.")
    if circle != base_geometry + spec.side_count:
        raise NativeSketchError(f"Sketch {spec.label} circle index changed.")
    constraint_count = spec.side_count * 3 - 1
    if constraint_indices != tuple(
        range(base_constraint, base_constraint + constraint_count)
    ):
        raise NativeSketchError(f"Sketch {spec.label} constraint indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=spec.side_count + 1,
        constraints_added=constraint_count,
    )

    side_records = [serialize_sketch_geometry(sketch, index) for index in sides]
    for index, record in enumerate(side_records):
        if (
            record.get("type_id") != "Part::GeomLineSegment"
            or record.get("kind") != "line"
            or bool(record.get("construction"))
            or bool(record.get("blocked"))
            or not same_sketch_point(record.get("start_mm"), spec.vertices_mm[index])
            or not same_sketch_point(
                record.get("end_mm"),
                spec.vertices_mm[(index + 1) % spec.side_count],
            )
        ):
            raise NativeSketchError(
                f"Sketch {spec.label} side differs from its definition."
            )

    circle_record = serialize_sketch_geometry(sketch, circle)
    if (
        circle_record.get("type_id") != "Part::GeomCircle"
        or circle_record.get("kind") != "circle"
        or circle_record.get("construction") is not True
        or bool(circle_record.get("blocked"))
        or circle_record.get("closed") is not True
        or not same_sketch_point(circle_record.get("center_mm"), spec.center_mm)
        or not same_sketch_vector(circle_record.get("axis"), (0.0, 0.0, 1.0))
        or not same_sketch_number(circle_record.get("radius_mm"), spec.radius_mm)
    ):
        raise NativeSketchError(
            f"Sketch {spec.label} construction Circle changed."
        )

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in constraint_indices
    ]
    for index, record in enumerate(constraints[: spec.side_count]):
        _active_constraint(
            record,
            "Coincident",
            [
                {
                    "slot": 1,
                    "geometry_index": sides[index],
                    "position": 2,
                },
                {
                    "slot": 2,
                    "geometry_index": sides[(index + 1) % spec.side_count],
                    "position": 1,
                },
            ],
            f"{spec.label} corner",
        )
    equality_start = spec.side_count
    equality_end = equality_start + spec.side_count - 1
    for record, side in zip(
        constraints[equality_start:equality_end],
        sides[1:],
        strict=True,
    ):
        _active_constraint(
            record,
            "Equal",
            [
                {"slot": 1, "geometry_index": sides[0]},
                {"slot": 2, "geometry_index": side},
            ],
            f"{spec.label} side equality",
        )
    for record, side in zip(
        constraints[equality_end:],
        sides,
        strict=True,
    ):
        _active_constraint(
            record,
            "PointOnObject",
            [
                {"slot": 1, "geometry_index": side, "position": 2},
                {"slot": 2, "geometry_index": circle},
            ],
            f"{spec.label} circumcircle incidence",
        )
    return sketch_geometry_result(
        sketch,
        {
            "geometries": side_records,
            "construction_circle": circle_record,
            "constraints": constraints,
            "center_mm": [*spec.center_mm, 0.0],
            "corner_mm": [*spec.corner_mm, 0.0],
            "vertices_mm": [[*point, 0.0] for point in spec.vertices_mm],
            "radius_mm": spec.radius_mm,
            "side_count": spec.side_count,
            "closed": True,
        },
    )
