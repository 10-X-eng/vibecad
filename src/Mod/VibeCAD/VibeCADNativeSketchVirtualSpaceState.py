# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state helpers for Sketch virtual-space operations."""

from __future__ import annotations

from typing import Any, Iterable

from VibeCADNativeSketchConstraintToggleState import (
    FrozenSketchConstraintToggleState,
    constraint_records_by_index,
    expected_constraint_state_records,
    read_sketch_constraint_toggle_state,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchVirtualSpaceTarget import (
    LABEL,
    SketchVirtualSpaceConstraintTarget,
    SketchVirtualSpaceSpec,
)


FrozenSketchVirtualSpaceState = FrozenSketchConstraintToggleState


def read_sketch_virtual_space_state(
    sketch: Any,
    spec: SketchVirtualSpaceSpec,
) -> FrozenSketchVirtualSpaceState:
    return read_sketch_constraint_toggle_state(sketch, spec, label=LABEL)


def validate_virtual_space_constraints(
    sketch: Any,
    spec: SketchVirtualSpaceSpec,
    state: FrozenSketchVirtualSpaceState,
    targets: tuple[SketchVirtualSpaceConstraintTarget, ...],
) -> tuple[dict[str, Any], ...]:
    records = constraint_records_by_index(state.constraint_records)
    try:
        constraints = list(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    if len(constraints) != spec.target.expected_constraint_count:
        raise NativeSketchError(
            "The active Sketch constraint count changed; read it and retry."
        )
    resolved = []
    for target in targets:
        record = records.get(target.constraint_index)
        if record is None:
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} is unavailable."
            )
        current = bool(record.get("virtual"))
        if current is not target.expected_virtual_space:
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} virtual-space state "
                "changed; read the current Sketch and retry."
            )
        if str(getattr(constraints[target.constraint_index], "Type", "")) != str(
            record.get("type", "")
        ):
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} type is inconsistent."
            )
        resolved.append(record)
    return tuple(resolved)


def expected_virtual_space_constraint_records(
    state: FrozenSketchVirtualSpaceState,
    targets: Iterable[SketchVirtualSpaceConstraintTarget],
) -> tuple[str, ...]:
    return expected_constraint_state_records(
        state,
        targets,
        record_field="virtual",
        target_field="virtual_space",
    )


def read_shown_virtual_space() -> bool:
    try:
        import SketcherGui

        value = SketcherGui.getActiveSketchVirtualSpace()
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} edit-view state is unavailable.") from exc
    if type(value) is not bool:
        raise NativeSketchError(f"{LABEL} edit-view state is invalid.")
    return value


def write_shown_virtual_space(shown: bool) -> None:
    if type(shown) is not bool:
        raise TypeError("shown must be a boolean")
    try:
        import SketcherGui

        result = SketcherGui.setActiveSketchVirtualSpace(shown)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} edit-view state could not be changed.") from exc
    if result is not shown:
        raise NativeSketchError(f"{LABEL} edit-view state did not reach its target.")
