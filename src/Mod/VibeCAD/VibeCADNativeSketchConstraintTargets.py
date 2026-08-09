# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact element targets and state guards for Native Sketch constraints."""

from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    prepare_active_sketch_target,
    require_prepared_active_sketch,
)


MAX_CONSTRAINT_SELECTION = 3
MAX_EXTERNAL_SKETCH_GEOMETRY = 1_000_000
POSITION_CODES = {"whole": 0, "start": 1, "end": 2, "center": 3}
_ELEMENT_FIELDS = frozenset({"geometry_index", "position"})


@dataclass(frozen=True, slots=True)
class SketchConstraintElement:
    geometry_index: int
    position: str

    @property
    def position_code(self) -> int:
        return POSITION_CODES[self.position]


@dataclass(frozen=True, slots=True)
class SketchConstraintTargetSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    selection: tuple[SketchConstraintElement, ...]
    allowed_internal_types: frozenset[str]


@dataclass(frozen=True, slots=True)
class PreparedSketchConstraintTarget:
    target: PreparedActiveSketchTarget
    spec: SketchConstraintTargetSpec
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]


def _external_count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_EXTERNAL_SKETCH_GEOMETRY:
        raise NativeSketchError(
            "Sketch expected_external_geometry_count must be an integer from 0 to "
            f"{MAX_EXTERNAL_SKETCH_GEOMETRY}."
        )
    return value


def _element(value: Any) -> SketchConstraintElement:
    if not isinstance(value, Mapping) or set(value) != _ELEMENT_FIELDS:
        raise NativeSketchError("A Sketch constraint element has incorrect fields.")
    index = value["geometry_index"]
    if (
        type(index) is not int
        or not (0 <= index < 1_000_000 or -1_000_000 <= index <= -1)
        or index == -2000
    ):
        raise NativeSketchError(
            "A Sketch constraint geometry_index must name internal geometry, an "
            "axis/root element, or external geometry."
        )
    position = value["position"]
    if not isinstance(position, str) or position not in POSITION_CODES:
        raise NativeSketchError(
            "A Sketch constraint position must be whole, start, end, or center."
        )
    return SketchConstraintElement(index, position)


def prepare_sketch_constraint_target(
    document_uid: str,
    *,
    sketch: Mapping[str, Any],
    expected_geometry_count: Any,
    expected_constraint_count: Any,
    expected_external_geometry_count: Any,
    selection: Any,
    maximum_selection: int = MAX_CONSTRAINT_SELECTION,
    allowed_internal_types: frozenset[str] = frozenset(),
) -> SketchConstraintTargetSpec:
    if type(maximum_selection) is not int or not 1 <= maximum_selection <= 17:
        raise TypeError(
            "maximum_selection must be an integer from one through seventeen"
        )
    if not isinstance(allowed_internal_types, frozenset) or not all(
        isinstance(value, str) and value for value in allowed_internal_types
    ):
        raise TypeError(
            "allowed_internal_types must be a frozenset of non-empty strings"
        )
    if not isinstance(selection, list) or not (
        1 <= len(selection) <= maximum_selection
    ):
        raise NativeSketchError(
            "Sketch constraint selection has an invalid number of exact elements."
        )
    elements = tuple(_element(value) for value in selection)
    identities = tuple(
        (element.geometry_index, element.position) for element in elements
    )
    if len(set(identities)) != len(identities):
        raise NativeSketchError("Sketch constraint elements must be distinct.")
    return SketchConstraintTargetSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=sketch,
            expected_geometry_count=expected_geometry_count,
            expected_constraint_count=expected_constraint_count,
        ),
        _external_count(expected_external_geometry_count),
        elements,
        allowed_internal_types,
    )


def _current_records(
    sketch: Any,
    spec: SketchConstraintTargetSpec,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    geometry = canonical_sketch_records(
        iter_sketch_geometry_records(sketch, spec.target.expected_geometry_count)
    )
    constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch, spec.target.expected_constraint_count)
    )
    external = canonical_sketch_records(iter_sketch_external_geometry_records(sketch))
    return geometry, constraints, external


def _records_by_index(
    records: tuple[str, ...],
    index_key: str,
) -> dict[int, dict[str, Any]]:
    return {
        int(record[index_key]): record
        for encoded in records
        for record in (json.loads(encoded),)
    }


def _group_members(sketch: Any) -> dict[int, int]:
    owners: dict[int, int] = {}
    try:
        constraints = list(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError("Sketch group constraints are unavailable.") from exc
    for constraint in constraints:
        if str(getattr(constraint, "Type", "")) not in {"Group", "Text"}:
            continue
        elements = getattr(constraint, "Elements", None)
        if not isinstance(elements, (list, tuple)) or not elements:
            raise NativeSketchError("Sketch group constraint elements are unavailable.")
        try:
            handle = int(elements[0][0])
            for raw in elements[1:]:
                member = int(raw[0])
                if member >= 0:
                    owners[member] = handle
        except (IndexError, TypeError, ValueError) as exc:
            raise NativeSketchError(
                "Sketch group constraint elements are malformed."
            ) from exc
    return owners


def _host_geometry(sketch: Any, index: int) -> Any:
    getter = getattr(sketch, "getGeometry", None)
    try:
        if callable(getter):
            geometry = getter(index)
        elif index in {-1, -2}:
            geometry = SimpleNamespace(TypeId="Part::GeomLineSegment")
        elif index <= -3:
            geometry = sketch.ExternalGeo[-index - 1]
        else:
            geometry = sketch.Geometry[index]
    except Exception as exc:
        raise NativeSketchError(f"Sketch geometry {index} is unavailable.") from exc
    if geometry is None:
        raise NativeSketchError(f"Sketch geometry {index} is unavailable.")
    return geometry


def _validate_position(
    sketch: Any,
    element: SketchConstraintElement,
    kind: str,
) -> None:
    index = element.geometry_index
    position = element.position
    if index == -1:
        if position not in {"whole", "start"}:
            raise NativeSketchError(
                "Sketch index -1 is the horizontal axis at whole or root point at start."
            )
        return
    if index == -2:
        if position != "whole":
            raise NativeSketchError("Sketch vertical axis -2 must use whole position.")
        return
    if position == "whole":
        return
    if kind == "point" and position != "start":
        raise NativeSketchError("Standalone Sketch points must use start position.")
    if kind == "line" and position not in {"start", "end"}:
        raise NativeSketchError("Sketch line points must use start or end position.")
    if kind == "circle" and position != "center":
        raise NativeSketchError("Sketch circle points must use center position.")
    if kind == "circular_arc" and position not in {"start", "end", "center"}:
        raise NativeSketchError(
            "Sketch circular-arc points must use start, end, or center position."
        )
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError("Sketch point lookup is unavailable.")
    try:
        getter(index, element.position_code)
    except Exception as exc:
        raise NativeSketchError(
            f"Sketch geometry {index} does not expose its {position} point."
        ) from exc


def _validate_selection(
    sketch: Any,
    spec: SketchConstraintTargetSpec,
    geometry_records: tuple[str, ...],
    external_records: tuple[str, ...],
) -> None:
    internal = _records_by_index(geometry_records, "index")
    external = _records_by_index(external_records, "geometry_index")
    group_members = _group_members(sketch)
    for element in spec.selection:
        index = element.geometry_index
        if index >= 0:
            record = internal.get(index)
            if record is None:
                raise NativeSketchError(
                    f"Sketch internal geometry index {index} is unavailable."
                )
            if index in group_members:
                raise NativeSketchError(
                    f"Sketch geometry {index} is selected through group handle "
                    f"{group_members[index]}; target that handle explicitly."
                )
            internal_type = str(record.get("internal_type", ""))
            if internal_type and internal_type not in spec.allowed_internal_types:
                raise NativeSketchError(
                    f"Sketch geometry {index} is internal-alignment geometry and "
                    "cannot be dimensioned directly."
                )
        elif index <= -3:
            record = external.get(index)
            if record is None:
                raise NativeSketchError(
                    f"Sketch external geometry index {index} is unavailable."
                )
            if bool(record.get("missing")) or bool(record.get("detached")):
                raise NativeSketchError(
                    f"Sketch external geometry {index} is missing or detached."
                )
        else:
            record = {"kind": "line"}
        _host_geometry(sketch, index)
        _validate_position(sketch, element, str(record.get("kind", "unknown")))


def preflight_sketch_constraint_target(
    context: NativeRuntimeContext,
    spec: SketchConstraintTargetSpec,
) -> PreparedSketchConstraintTarget:
    if not isinstance(spec, SketchConstraintTargetSpec):
        raise TypeError("spec must be a SketchConstraintTargetSpec")
    target = preflight_active_sketch(context, spec.target)
    geometry, constraints, external = _current_records(target.sketch, spec)
    if len(external) != spec.expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; read its current "
            "state and retry."
        )
    _validate_selection(target.sketch, spec, geometry, external)
    return PreparedSketchConstraintTarget(
        target,
        spec,
        geometry,
        constraints,
        external,
    )


def require_unchanged_sketch_constraint_target(
    document: Any,
    prepared: PreparedSketchConstraintTarget,
    *,
    stage: str,
) -> Any:
    if not isinstance(prepared, PreparedSketchConstraintTarget):
        raise TypeError("prepared must be a PreparedSketchConstraintTarget")
    sketch = require_prepared_active_sketch(document, prepared.target)
    geometry, constraints, external = _current_records(sketch, prepared.spec)
    if (
        geometry != prepared.geometry_records
        or constraints != prepared.constraint_records
        or external != prepared.external_geometry_records
    ):
        raise NativeSketchError(f"The active Sketch changed {stage}.")
    return sketch


def current_sketch_constraint_records(
    sketch: Any,
    spec: SketchConstraintTargetSpec,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    geometry = canonical_sketch_records(
        iter_sketch_geometry_records(sketch, spec.target.expected_geometry_count)
    )
    constraints = canonical_sketch_records(iter_sketch_constraint_records(sketch))
    external = canonical_sketch_records(iter_sketch_external_geometry_records(sketch))
    return geometry, constraints, external


def sketch_constraint_geometry(sketch: Any, index: int) -> Any:
    return _host_geometry(sketch, index)
