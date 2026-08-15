# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Symmetry command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)
from VibeCADNativeSketchTransformTarget import MAX_TRANSFORM_INSTANCES


LABEL = "Sketch Symmetry"
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_indices",
        "reference",
        "source_mode",
    }
)
REFERENCE_FIELDS = frozenset({"geometry_index", "position"})
POSITION_CODES = {"whole": 0, "start": 1, "end": 2, "center": 3}
SOURCE_MODES = frozenset({"keep", "delete", "constrain"})
SOURCE_VALUES = {"keep": 0, "delete": 1, "constrain": 2}


@dataclass(frozen=True, slots=True)
class SketchSymmetryReference:
    geometry_index: int
    position: str

    @property
    def position_code(self) -> int:
        return POSITION_CODES[self.position]


@dataclass(frozen=True, slots=True)
class SketchSymmetrySpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_indices: tuple[int, ...]
    reference: SketchSymmetryReference
    source_mode: str

    @property
    def source_value(self) -> int:
        return SOURCE_VALUES[self.source_mode]


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _geometry_indices(value: Any) -> tuple[int, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not 1 <= len(value) <= MAX_TRANSFORM_INSTANCES
        or any(
            type(index) is not int
            or not -MAX_SKETCH_ELEMENTS <= index < MAX_SKETCH_ELEMENTS
            for index in value
        )
        or len(set(value)) != len(value)
    ):
        raise NativeSketchError(
            f"{LABEL} requires a bounded ordered list of unique geometry indices."
        )
    return tuple(value)


def _reference(value: Any) -> SketchSymmetryReference:
    if not isinstance(value, Mapping) or set(value) != REFERENCE_FIELDS:
        raise NativeSketchError(f"{LABEL} reference has incorrect fields.")
    index = value["geometry_index"]
    if (
        type(index) is not int
        or not -MAX_SKETCH_ELEMENTS <= index < MAX_SKETCH_ELEMENTS
        or index == -2000
    ):
        raise NativeSketchError(
            f"{LABEL} reference geometry_index is outside the bounded Sketch range."
        )
    position = value["position"]
    if not isinstance(position, str) or position not in POSITION_CODES:
        raise NativeSketchError(
            f"{LABEL} reference position must be whole, start, end, or center."
        )
    return SketchSymmetryReference(index, position)


def prepare_sketch_symmetry(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchSymmetrySpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    source_mode = value["source_mode"]
    if not isinstance(source_mode, str) or source_mode not in SOURCE_MODES:
        raise NativeSketchError(
            f"{LABEL} source mode must be keep, delete, or constrain."
        )
    return SketchSymmetrySpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        _geometry_indices(value["geometry_indices"]),
        _reference(value["reference"]),
        source_mode,
    )
