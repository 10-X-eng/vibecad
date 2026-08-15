# SPDX-License-Identifier: LGPL-2.1-or-later

"""Driving/Reference validation over shared exact toggle state."""

from __future__ import annotations

from typing import Any, Iterable

from VibeCADNativeSketchConstraintToggleState import (
    FrozenSketchConstraintToggleState,
    SketchExpressionRecord,
    constraint_records_by_index,
    expected_constraint_state_records,
    read_sketch_constraint_toggle_state,
    sketch_geometry_metadata as sketch_geometry_metadata,
)
from VibeCADNativeSketchDrivingTarget import (
    LABEL,
    SketchDrivingSpec,
    SketchDrivingTarget,
)
from VibeCADNativeSketchErrors import NativeSketchError


DIMENSIONAL_CONSTRAINT_TYPES = frozenset(
    {
        "Distance",
        "DistanceX",
        "DistanceY",
        "Radius",
        "Diameter",
        "Angle",
        "SnellsLaw",
        "Weight",
    }
)
FrozenSketchDrivingState = FrozenSketchConstraintToggleState


def read_sketch_driving_state(
    sketch: Any,
    spec: SketchDrivingSpec,
) -> FrozenSketchDrivingState:
    return read_sketch_constraint_toggle_state(sketch, spec, label=LABEL)


def validate_sketch_driving_targets(
    sketch: Any,
    spec: SketchDrivingSpec,
    state: FrozenSketchDrivingState,
) -> tuple[dict[str, Any], ...]:
    records = constraint_records_by_index(state.constraint_records)
    try:
        raw_constraints = list(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    if len(raw_constraints) != spec.target.expected_constraint_count:
        raise NativeSketchError(
            "The active Sketch constraint count changed; read it and retry."
        )
    resolved = []
    for target in spec.targets:
        record = records.get(target.constraint_index)
        if record is None:
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} is unavailable."
            )
        constraint_type = str(record.get("type", ""))
        if constraint_type not in DIMENSIONAL_CONSTRAINT_TYPES:
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} is not dimensional."
            )
        current = bool(record.get("driving"))
        if current is not target.expected_driving:
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} driving state changed; "
                "read the current Sketch and retry."
            )
        raw = raw_constraints[target.constraint_index]
        if str(getattr(raw, "Type", "")) != constraint_type:
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} type is inconsistent."
            )
        if target.driving:
            try:
                references = tuple(
                    int(getattr(raw, name)) for name in ("First", "Second", "Third")
                )
            except Exception as exc:
                raise NativeSketchError(
                    f"Sketch constraint {target.constraint_index} references are unavailable."
                ) from exc
            if not any(index >= 0 for index in references):
                raise NativeSketchError(
                    f"Sketch constraint {target.constraint_index} references only axes or "
                    "external geometry and cannot become driving."
                )
        resolved.append(record)
    return tuple(resolved)


def expected_constraint_records(
    state: FrozenSketchDrivingState,
    targets: Iterable[SketchDrivingTarget],
) -> tuple[str, ...]:
    return expected_constraint_state_records(
        state,
        targets,
        record_field="driving",
        target_field="driving",
    )


def expected_expression_records(
    state: FrozenSketchDrivingState,
    targets: Iterable[SketchDrivingTarget],
) -> tuple[SketchExpressionRecord, ...]:
    removed = {target.constraint_index for target in targets if not target.driving}
    return tuple(
        record
        for record in state.expression_records
        if record.constraint_index not in removed
    )
