# SPDX-License-Identifier: LGPL-2.1-or-later

"""Active/Inactive validation over shared exact toggle state."""

from __future__ import annotations

from typing import Any, Iterable

from VibeCADNativeSketchActiveTarget import (
    LABEL,
    SketchActiveSpec,
    SketchActiveTarget,
)
from VibeCADNativeSketchConstraintToggleState import (
    FrozenSketchConstraintToggleState,
    constraint_records_by_index,
    expected_constraint_state_records,
    read_sketch_constraint_toggle_state,
    sketch_geometry_metadata as sketch_geometry_metadata,
)
from VibeCADNativeSketchErrors import NativeSketchError


FrozenSketchActiveState = FrozenSketchConstraintToggleState


def read_sketch_active_state(
    sketch: Any,
    spec: SketchActiveSpec,
) -> FrozenSketchActiveState:
    return read_sketch_constraint_toggle_state(sketch, spec, label=LABEL)


def validate_sketch_active_targets(
    sketch: Any,
    spec: SketchActiveSpec,
    state: FrozenSketchActiveState,
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
        current = bool(record.get("active"))
        if current is not target.expected_active:
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} active state changed; "
                "read the current Sketch and retry."
            )
        constraint_type = str(record.get("type", ""))
        raw = raw_constraints[target.constraint_index]
        if str(getattr(raw, "Type", "")) != constraint_type:
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} type is inconsistent."
            )
        resolved.append(record)
    return tuple(resolved)


def expected_constraint_records(
    state: FrozenSketchActiveState,
    targets: Iterable[SketchActiveTarget],
) -> tuple[str, ...]:
    return expected_constraint_state_records(
        state,
        targets,
        record_field="active",
        target_field="active",
    )
