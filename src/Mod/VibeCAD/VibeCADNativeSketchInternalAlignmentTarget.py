# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed targets for the durable Sketch internal-geometry toggle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)


LABEL = "Internal-alignment geometry toggle"
OPERATION = "restore_internal_alignment_geometry"
MAX_TARGETS = 64
MAX_SKETCH_ELEMENTS = 1_000_000
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "targets",
    }
)
TARGET_FIELDS = frozenset({"geometry_index", "expected_internal_geometry_count"})


@dataclass(frozen=True, slots=True)
class InternalAlignmentTarget:
    geometry_index: int
    expected_internal_geometry_count: int


@dataclass(frozen=True, slots=True)
class SketchInternalAlignmentSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    targets: tuple[InternalAlignmentTarget, ...]


def _count(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {label} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _target(value: Any) -> InternalAlignmentTarget:
    if not isinstance(value, Mapping) or set(value) != TARGET_FIELDS:
        raise NativeSketchError(f"A {LABEL} target has incorrect fields.")
    index = value["geometry_index"]
    if type(index) is not int or not 0 <= index < MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"A {LABEL} geometry_index must be a non-negative Sketch index."
        )
    return InternalAlignmentTarget(
        index,
        _count(
            value["expected_internal_geometry_count"],
            "expected internal geometry count",
        ),
    )


def prepare_sketch_internal_alignment(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchInternalAlignmentSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    raw_targets = value["targets"]
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= MAX_TARGETS:
        raise NativeSketchError(
            f"{LABEL} targets must contain 1 through {MAX_TARGETS} exact curves."
        )
    targets = tuple(_target(item) for item in raw_targets)
    indices = tuple(item.geometry_index for item in targets)
    if len(set(indices)) != len(indices):
        raise NativeSketchError(f"{LABEL} targets must be distinct.")
    return SketchInternalAlignmentSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(
            value["expected_external_reference_count"],
            "expected external reference count",
        ),
        _count(
            value["expected_external_geometry_count"],
            "expected external geometry count",
        ),
        targets,
    )
