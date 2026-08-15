# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Join Curves command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)


LABEL = "Sketch Join Curves"
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "first",
        "second",
    }
)
ENDPOINT_FIELDS = frozenset({"geometry_index", "endpoint"})
ENDPOINT_CODES = {"start": 1, "end": 2}


@dataclass(frozen=True, slots=True)
class SketchJoinEndpoint:
    geometry_index: int
    endpoint: str

    @property
    def endpoint_code(self) -> int:
        return ENDPOINT_CODES[self.endpoint]


@dataclass(frozen=True, slots=True)
class SketchJoinSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    first: SketchJoinEndpoint
    second: SketchJoinEndpoint

    @property
    def geometry_indices(self) -> tuple[int, int]:
        return self.first.geometry_index, self.second.geometry_index


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _endpoint(value: Any, field: str) -> SketchJoinEndpoint:
    if not isinstance(value, Mapping) or set(value) != ENDPOINT_FIELDS:
        raise NativeSketchError(f"{LABEL} {field} endpoint has incorrect fields.")
    index = value["geometry_index"]
    endpoint = value["endpoint"]
    if type(index) is not int or not 0 <= index < MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} geometry_index must be an integer from 0 to 999999."
        )
    if not isinstance(endpoint, str) or endpoint not in ENDPOINT_CODES:
        raise NativeSketchError(f"{LABEL} {field} endpoint must be start or end.")
    return SketchJoinEndpoint(index, endpoint)


def prepare_sketch_join_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchJoinSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    first = _endpoint(value["first"], "first")
    second = _endpoint(value["second"], "second")
    if first.geometry_index == second.geometry_index:
        raise NativeSketchError(f"{LABEL} requires two distinct curves.")
    return SketchJoinSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        first,
        second,
    )
