# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact relationship state shared by Sketch inspect reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeSketchConstraintToggleState import (
    FrozenSketchConstraintToggleState,
    read_sketch_constraint_toggle_state,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInspectTarget import POSITION_CODES
from VibeCADNativeSketchState import iter_sketch_external_geometry_records


MAX_CONSTRAINT_ELEMENTS = 256
MAX_TOTAL_CONSTRAINT_ELEMENTS = 4096


@dataclass(frozen=True, slots=True)
class SketchConstraintLink:
    index: int
    tag: str
    constraint_type: str
    name: str
    elements: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class FrozenConstraintRelationshipState:
    sketch_state: FrozenSketchConstraintToggleState
    links: tuple[SketchConstraintLink, ...]
    external_geometry_count: int


def _constraint_links(
    sketch: Any,
    expected_count: int,
) -> tuple[SketchConstraintLink, ...]:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(
            "Sketch constraints are unavailable for relationship lookup."
        ) from exc
    if len(constraints) != expected_count:
        raise NativeSketchError(
            "The active Sketch constraint count changed; read its current state and retry."
        )
    links = []
    tags: set[str] = set()
    total_elements = 0
    for index, constraint in enumerate(constraints):
        tag = str(getattr(constraint, "Tag", "") or "")
        if not tag or len(tag) > 128 or tag in tags:
            raise NativeSketchError(
                "Sketch constraints do not expose unique bounded live identities."
            )
        tags.add(tag)
        raw_elements = getattr(constraint, "Elements", None)
        if (
            not isinstance(raw_elements, (list, tuple))
            or len(raw_elements) > MAX_CONSTRAINT_ELEMENTS
        ):
            raise NativeSketchError(
                "A Sketch constraint has unbounded element references."
            )
        elements = []
        for raw in raw_elements:
            if isinstance(raw, (list, tuple)) and tuple(raw) == (-2000, 0):
                continue
            if (
                not isinstance(raw, (list, tuple))
                or len(raw) != 2
                or type(raw[0]) is not int
                or type(raw[1]) is not int
                or raw[0] <= -2000
                or raw[1] not in POSITION_CODES.values()
            ):
                raise NativeSketchError(
                    "A Sketch constraint has malformed element references."
                )
            elements.append((raw[0], raw[1]))
        total_elements += len(elements)
        if total_elements > MAX_TOTAL_CONSTRAINT_ELEMENTS:
            raise NativeSketchError(
                "Sketch constraint relationships exceed the bounded read size."
            )
        constraint_type = str(getattr(constraint, "Type", "") or "")
        name = str(getattr(constraint, "Name", "") or "")
        if not constraint_type or len(constraint_type) > 96 or len(name) > 128:
            raise NativeSketchError(
                "A Sketch constraint has unbounded descriptive state."
            )
        links.append(
            SketchConstraintLink(
                index,
                tag,
                constraint_type,
                name,
                tuple(elements),
            )
        )
    return tuple(links)


def freeze_constraint_relationship_state(
    sketch: Any,
    spec: Any,
    *,
    label: str,
) -> FrozenConstraintRelationshipState:
    external_count = sum(1 for _item in iter_sketch_external_geometry_records(sketch))
    if external_count != spec.expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; read its current state and retry."
        )
    return FrozenConstraintRelationshipState(
        read_sketch_constraint_toggle_state(sketch, spec, label=label),
        _constraint_links(sketch, spec.target.expected_constraint_count),
        external_count,
    )
