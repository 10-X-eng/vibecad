# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for Native Sketch Carbon Copy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)
from VibeCADNativeTargets import NativeObjectRef, NativeTargetError


FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "source_sketch",
        "expected_source_geometry_count",
        "expected_source_constraint_count",
        "expected_source_external_reference_count",
        "expected_source_external_geometry_count",
        "geometry_mode",
        "reference_permission",
    }
)
_SOURCE_FIELDS = frozenset({"object_name"})
_GEOMETRY_MODES = frozenset({"construction", "regular"})
_REFERENCE_PERMISSIONS = {
    "same_body_aligned": (False, False),
    "cross_body_aligned": (True, False),
    "unaligned": (True, True),
}


@dataclass(frozen=True, slots=True)
class SketchCarbonCopySpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    source: NativeObjectRef
    expected_source_geometry_count: int
    expected_source_constraint_count: int
    expected_source_external_reference_count: int
    expected_source_external_geometry_count: int
    construction: bool
    reference_permission: str
    allow_other_body: bool
    allow_unaligned: bool


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"Carbon Copy {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def prepare_sketch_carbon_copy(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchCarbonCopySpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError("A Carbon Copy definition has incorrect fields.")
    raw_source = value["source_sketch"]
    if not isinstance(raw_source, Mapping) or set(raw_source) != _SOURCE_FIELDS:
        raise NativeSketchError("A Carbon Copy source Sketch has incorrect fields.")
    try:
        source = NativeObjectRef(
            str(document_uid or ""),
            str(raw_source["object_name"] or ""),
        )
    except NativeTargetError as exc:
        raise NativeSketchError(
            "Carbon Copy requires one exact source Sketch."
        ) from exc
    geometry_mode = value["geometry_mode"]
    if geometry_mode not in _GEOMETRY_MODES:
        raise NativeSketchError(
            "Carbon Copy geometry mode must be 'construction' or 'regular'."
        )
    permission = value["reference_permission"]
    options = _REFERENCE_PERMISSIONS.get(permission)
    if options is None:
        raise NativeSketchError(
            "Carbon Copy reference permission must match one human modifier mode."
        )
    return SketchCarbonCopySpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        source,
        _count(value["expected_source_geometry_count"], "source geometry count"),
        _count(value["expected_source_constraint_count"], "source constraint count"),
        _count(
            value["expected_source_external_reference_count"],
            "source external reference count",
        ),
        _count(
            value["expected_source_external_geometry_count"],
            "source external geometry count",
        ),
        geometry_mode == "construction",
        permission,
        options[0],
        options[1],
    )
