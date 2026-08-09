# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Remove Axes Alignment command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)

LABEL = "Remove Axes Alignment"
MAX_AXIS_ALIGNMENT_TARGETS = 256
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_indices",
    }
)


@dataclass(frozen=True, slots=True)
class SketchAxisAlignmentSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_indices: tuple[int, ...]


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _geometry_indices(value: Any, expected_count: int) -> tuple[int, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not 1 <= len(value) <= MAX_AXIS_ALIGNMENT_TARGETS
        or any(
            type(index) is not int or not 0 <= index < expected_count for index in value
        )
        or len(set(value)) != len(value)
    ):
        raise NativeSketchError(
            f"{LABEL} requires a bounded ordered list of unique current internal "
            "geometry indices."
        )
    return tuple(value)


def prepare_sketch_axis_alignment(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchAxisAlignmentSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    target = prepare_active_sketch_target(
        document_uid,
        sketch=value["sketch"],
        expected_geometry_count=value["expected_geometry_count"],
        expected_constraint_count=value["expected_constraint_count"],
    )
    return SketchAxisAlignmentSpec(
        target,
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        _geometry_indices(value["geometry_indices"], target.expected_geometry_count),
    )
