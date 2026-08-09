# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact targets for Native Sketch Driving/Reference toggles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)


LABEL = "Sketch Driving/Reference Toggle"
MAX_DRIVING_TARGETS = 16
MAX_EXTERNAL_SKETCH_GEOMETRY = 1_000_000
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "targets",
    }
)
_TARGET_FIELDS = frozenset({"constraint_index", "expected_driving"})


@dataclass(frozen=True, slots=True)
class SketchDrivingTarget:
    constraint_index: int
    expected_driving: bool

    @property
    def driving(self) -> bool:
        return not self.expected_driving


@dataclass(frozen=True, slots=True)
class SketchDrivingSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    targets: tuple[SketchDrivingTarget, ...]


def _external_count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_EXTERNAL_SKETCH_GEOMETRY:
        raise NativeSketchError(
            "Sketch expected_external_geometry_count must be an integer from 0 to "
            f"{MAX_EXTERNAL_SKETCH_GEOMETRY}."
        )
    return value


def _target(value: Any) -> SketchDrivingTarget:
    if not isinstance(value, Mapping) or set(value) != _TARGET_FIELDS:
        raise NativeSketchError(f"A {LABEL} target has incorrect fields.")
    index = value["constraint_index"]
    if type(index) is not int or not 0 <= index < 1_000_000:
        raise NativeSketchError(
            f"A {LABEL} constraint_index must be an integer from 0 to 999999."
        )
    expected = value["expected_driving"]
    if type(expected) is not bool:
        raise NativeSketchError(
            f"A {LABEL} expected_driving state must be a boolean."
        )
    return SketchDrivingTarget(index, expected)


def prepare_sketch_driving_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchDrivingSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    raw_targets = value["targets"]
    if not isinstance(raw_targets, list) or not (
        1 <= len(raw_targets) <= MAX_DRIVING_TARGETS
    ):
        raise NativeSketchError(
            f"{LABEL} targets must contain one through {MAX_DRIVING_TARGETS} "
            "exact constraint targets."
        )
    targets = tuple(_target(raw) for raw in raw_targets)
    indices = tuple(target.constraint_index for target in targets)
    if len(set(indices)) != len(indices):
        raise NativeSketchError(f"{LABEL} constraint targets must be distinct.")
    return SketchDrivingSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _external_count(value["expected_external_geometry_count"]),
        targets,
    )
