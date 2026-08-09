# SPDX-License-Identifier: LGPL-2.1-or-later

"""Validated client-local plan for one atomic Native Sketch batch."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    require_distinct_points,
    sketch_bounded_parameter,
    sketch_point_2d,
    sketch_positive_length,
    sketch_start_angle_degrees,
    sketch_sweep_angle_degrees,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)


MAX_BATCH_GEOMETRY = 32
MAX_BATCH_CONSTRAINTS = 16
BATCH_LOCAL_REF_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,31}$"
_LOCAL_REF = re.compile(BATCH_LOCAL_REF_PATTERN)
_OUTER_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "geometry",
        "constraints",
    }
)
_POINT_POSITIONS = {
    "point": frozenset({"point"}),
    "line": frozenset({"start", "end"}),
    "circle": frozenset({"center"}),
    "arc": frozenset({"start", "end", "center"}),
}
_CIRCULAR_KINDS = frozenset({"circle", "arc"})


@dataclass(frozen=True, slots=True)
class SketchBatchGeometrySpec:
    local_ref: str
    kind: str
    construction: bool
    first_mm: tuple[float, float]
    second_mm: tuple[float, float] | None = None
    radius_mm: float | None = None
    start_angle_degrees: float | None = None
    sweep_angle_degrees: float | None = None


@dataclass(frozen=True, slots=True)
class SketchBatchPointRef:
    geometry_ref: str | None
    position: str

    @property
    def is_origin(self) -> bool:
        return self.geometry_ref is None


@dataclass(frozen=True, slots=True)
class SketchBatchConstraintSpec:
    local_ref: str
    kind: str
    points: tuple[SketchBatchPointRef, ...] = ()
    geometry_refs: tuple[str, ...] = ()
    value: float | None = None


@dataclass(frozen=True, slots=True)
class SketchBatchSpec:
    target: ActiveSketchTargetSpec
    geometry: tuple[SketchBatchGeometrySpec, ...]
    constraints: tuple[SketchBatchConstraintSpec, ...]


def _local_ref(value: Any, label: str) -> str:
    if not isinstance(value, str) or _LOCAL_REF.fullmatch(value) is None:
        raise NativeSketchError(
            f"Sketch batch {label} must start with a letter and contain at most "
            "32 letters, digits, or underscores."
        )
    return value


def _construction(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise NativeSketchError(f"Sketch batch {label} must be a boolean.")
    return value


def _geometry_spec(value: Any, offset: int) -> SketchBatchGeometrySpec:
    if not isinstance(value, Mapping):
        raise NativeSketchError(f"Sketch batch geometry {offset} must be an object.")
    kind = str(value.get("kind", "") or "")
    local_ref = _local_ref(value.get("ref"), f"geometry {offset} ref")
    construction = _construction(
        value.get("construction"), f"geometry {local_ref} construction"
    )
    prefix = f"geometry {local_ref}"
    if kind == "point" and set(value) == {
        "ref",
        "kind",
        "construction",
        "position_mm",
    }:
        return SketchBatchGeometrySpec(
            local_ref,
            kind,
            construction,
            sketch_point_2d(value["position_mm"], f"{prefix} position_mm"),
        )
    if kind == "line" and set(value) == {
        "ref",
        "kind",
        "construction",
        "start_mm",
        "end_mm",
    }:
        start = sketch_point_2d(value["start_mm"], f"{prefix} start_mm")
        end = sketch_point_2d(value["end_mm"], f"{prefix} end_mm")
        require_distinct_points(start, end, f"batch {local_ref} Line")
        return SketchBatchGeometrySpec(
            local_ref,
            kind,
            construction,
            start,
            end,
        )
    if kind == "circle" and set(value) == {
        "ref",
        "kind",
        "construction",
        "center_mm",
        "radius_mm",
    }:
        return SketchBatchGeometrySpec(
            local_ref,
            kind,
            construction,
            sketch_point_2d(value["center_mm"], f"{prefix} center_mm"),
            radius_mm=sketch_positive_length(
                value["radius_mm"], f"batch {local_ref} Circle radius_mm"
            ),
        )
    if kind == "arc" and set(value) == {
        "ref",
        "kind",
        "construction",
        "center_mm",
        "radius_mm",
        "start_angle_degrees",
        "sweep_angle_degrees",
    }:
        return SketchBatchGeometrySpec(
            local_ref,
            kind,
            construction,
            sketch_point_2d(value["center_mm"], f"{prefix} center_mm"),
            radius_mm=sketch_positive_length(
                value["radius_mm"], f"batch {local_ref} Arc radius_mm"
            ),
            start_angle_degrees=sketch_start_angle_degrees(
                value["start_angle_degrees"],
                f"batch {local_ref} Arc start_angle_degrees",
            ),
            sweep_angle_degrees=sketch_sweep_angle_degrees(
                value["sweep_angle_degrees"],
                f"batch {local_ref} Arc sweep_angle_degrees",
            ),
        )
    raise NativeSketchError(
        f"Sketch batch geometry {local_ref} has fields inconsistent with kind "
        "point, line, circle, or arc."
    )


def _point_ref(value: Any, label: str) -> SketchBatchPointRef:
    if not isinstance(value, Mapping):
        raise NativeSketchError(f"Sketch batch {label} must be a point reference.")
    if set(value) == {"origin"} and value["origin"] is True:
        return SketchBatchPointRef(None, "origin")
    if set(value) != {"geometry_ref", "position"}:
        raise NativeSketchError(f"Sketch batch {label} has incorrect fields.")
    position = value["position"]
    if not isinstance(position, str) or position not in {
        "point",
        "start",
        "end",
        "center",
    }:
        raise NativeSketchError(f"Sketch batch {label} has an invalid point position.")
    return SketchBatchPointRef(
        _local_ref(value["geometry_ref"], f"{label} geometry_ref"),
        position,
    )


def _signed_mm(value: Any, label: str) -> float:
    return sketch_bounded_parameter(value, label, maximum_absolute=1_000_000.0)


def _positive_mm(value: Any, label: str) -> float:
    return sketch_positive_length(value, label)


def _angle(value: Any, label: str) -> float:
    result = sketch_bounded_parameter(value, label, maximum_absolute=360.0)
    if result < -180.0:
        raise NativeSketchError(f"Sketch batch {label} must be from -180 to 360 degrees.")
    return result


def _constraint_spec(value: Any, offset: int) -> SketchBatchConstraintSpec:
    if not isinstance(value, Mapping):
        raise NativeSketchError(f"Sketch batch constraint {offset} must be an object.")
    kind = str(value.get("kind", "") or "")
    local_ref = _local_ref(value.get("ref"), f"constraint {offset} ref")
    prefix = f"constraint {local_ref}"
    if kind == "coincident" and set(value) == {"ref", "kind", "first", "second"}:
        return SketchBatchConstraintSpec(
            local_ref,
            kind,
            (_point_ref(value["first"], f"{prefix} first"),
             _point_ref(value["second"], f"{prefix} second")),
        )
    if kind in {"horizontal", "vertical", "radius", "diameter"}:
        expected = {"ref", "kind", "geometry_ref"}
        if kind in {"radius", "diameter"}:
            expected.add("value_mm")
        if set(value) == expected:
            return SketchBatchConstraintSpec(
                local_ref,
                kind,
                geometry_refs=(
                    _local_ref(value["geometry_ref"], f"{prefix} geometry_ref"),
                ),
                value=(
                    _positive_mm(value["value_mm"], f"{prefix} value_mm")
                    if "value_mm" in value
                    else None
                ),
            )
    if kind in {"parallel", "perpendicular", "equal", "angle"}:
        expected = {"ref", "kind", "first_geometry_ref", "second_geometry_ref"}
        if kind == "angle":
            expected.add("value_degrees")
        if set(value) == expected:
            return SketchBatchConstraintSpec(
                local_ref,
                kind,
                geometry_refs=(
                    _local_ref(
                        value["first_geometry_ref"],
                        f"{prefix} first_geometry_ref",
                    ),
                    _local_ref(
                        value["second_geometry_ref"],
                        f"{prefix} second_geometry_ref",
                    ),
                ),
                value=(
                    _angle(value["value_degrees"], f"{prefix} value_degrees")
                    if "value_degrees" in value
                    else None
                ),
            )
    if kind in {"distance_x", "distance_y", "distance"} and set(value) == {
        "ref",
        "kind",
        "first",
        "second",
        "value_mm",
    }:
        return SketchBatchConstraintSpec(
            local_ref,
            kind,
            (_point_ref(value["first"], f"{prefix} first"),
             _point_ref(value["second"], f"{prefix} second")),
            value=(
                _positive_mm(value["value_mm"], f"{prefix} value_mm")
                if kind == "distance"
                else _signed_mm(value["value_mm"], f"{prefix} value_mm")
            ),
        )
    raise NativeSketchError(
        f"Sketch batch constraint {local_ref} has unsupported or inconsistent fields."
    )


def _unique_refs(values: tuple[Any, ...], label: str) -> None:
    refs = tuple(value.local_ref for value in values)
    if len(refs) != len(set(refs)):
        raise NativeSketchError(f"Sketch batch {label} refs must be unique.")


def _validate_point_ref(
    reference: SketchBatchPointRef,
    geometry: Mapping[str, SketchBatchGeometrySpec],
    label: str,
) -> None:
    if reference.is_origin:
        return
    target = geometry.get(str(reference.geometry_ref))
    if target is None:
        raise NativeSketchError(
            f"Sketch batch {label} references unknown geometry {reference.geometry_ref!r}."
        )
    if reference.position not in _POINT_POSITIONS[target.kind]:
        allowed = ", ".join(sorted(_POINT_POSITIONS[target.kind]))
        raise NativeSketchError(
            f"Sketch batch {label} must use {allowed} on {target.kind} geometry."
        )


def _geometry_target(
    local_ref: str,
    geometry: Mapping[str, SketchBatchGeometrySpec],
    label: str,
) -> SketchBatchGeometrySpec:
    target = geometry.get(local_ref)
    if target is None:
        raise NativeSketchError(
            f"Sketch batch {label} references unknown geometry {local_ref!r}."
        )
    return target


def _validate_constraint_refs(
    constraint: SketchBatchConstraintSpec,
    geometry: Mapping[str, SketchBatchGeometrySpec],
) -> None:
    label = f"constraint {constraint.local_ref}"
    for point in constraint.points:
        _validate_point_ref(point, geometry, label)
    if constraint.points and len(set(constraint.points)) != len(constraint.points):
        raise NativeSketchError(f"Sketch batch {label} cannot repeat one exact point.")
    targets = tuple(
        _geometry_target(local_ref, geometry, label)
        for local_ref in constraint.geometry_refs
    )
    if len(targets) > 1 and len(set(constraint.geometry_refs)) != len(targets):
        raise NativeSketchError(f"Sketch batch {label} requires distinct geometry.")
    if constraint.kind in {"horizontal", "vertical", "parallel", "perpendicular", "angle"}:
        if any(target.kind != "line" for target in targets):
            raise NativeSketchError(f"Sketch batch {label} supports only line geometry.")
    elif constraint.kind == "equal":
        kinds = {target.kind for target in targets}
        if not (kinds == {"line"} or kinds <= _CIRCULAR_KINDS):
            raise NativeSketchError(
                f"Sketch batch {label} requires two lines or two circular curves."
            )
    elif constraint.kind in {"radius", "diameter"} and any(
        target.kind not in _CIRCULAR_KINDS for target in targets
    ):
        raise NativeSketchError(
            f"Sketch batch {label} requires one circle or circular arc."
        )


def prepare_sketch_batch(document_uid: str, value: Mapping[str, Any]) -> SketchBatchSpec:
    """Validate all client-local references before any document mutation."""

    if not isinstance(value, Mapping) or set(value) != _OUTER_FIELDS:
        raise NativeSketchError("A Sketch batch definition has incorrect fields.")
    raw_geometry = value["geometry"]
    raw_constraints = value["constraints"]
    if not isinstance(raw_geometry, list) or not (
        1 <= len(raw_geometry) <= MAX_BATCH_GEOMETRY
    ):
        raise NativeSketchError(
            f"Sketch batch geometry must contain 1 to {MAX_BATCH_GEOMETRY} items."
        )
    if not isinstance(raw_constraints, list) or not (
        1 <= len(raw_constraints) <= MAX_BATCH_CONSTRAINTS
    ):
        raise NativeSketchError(
            f"Sketch batch constraints must contain 1 to {MAX_BATCH_CONSTRAINTS} items."
        )
    geometry = tuple(
        _geometry_spec(item, offset) for offset, item in enumerate(raw_geometry)
    )
    constraints = tuple(
        _constraint_spec(item, offset)
        for offset, item in enumerate(raw_constraints)
    )
    _unique_refs(geometry, "geometry")
    _unique_refs(constraints, "constraint")
    by_ref = {item.local_ref: item for item in geometry}
    for constraint in constraints:
        _validate_constraint_refs(constraint, by_ref)
    target = prepare_active_sketch_target(
        document_uid,
        sketch=value["sketch"],
        expected_geometry_count=value["expected_geometry_count"],
        expected_constraint_count=value["expected_constraint_count"],
    )
    if target.expected_geometry_count + len(geometry) > MAX_SKETCH_ELEMENTS:
        raise NativeSketchError("Sketch batch would exceed the geometry-count limit.")
    if target.expected_constraint_count + len(constraints) > MAX_SKETCH_ELEMENTS:
        raise NativeSketchError("Sketch batch would exceed the constraint-count limit.")
    if any(
        item.value is not None and not math.isfinite(item.value)
        for item in constraints
    ):
        raise NativeSketchError("Sketch batch constraint values must be finite.")
    return SketchBatchSpec(target, geometry, constraints)
