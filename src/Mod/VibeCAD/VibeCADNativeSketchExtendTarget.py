# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Extend command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchCurvePointTarget import (
    SketchCurvePointSpec,
    prepare_sketch_curve_point_target,
)
from VibeCADNativeSketchErrors import NativeSketchError


LABEL = "Sketch Extend"
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "target",
    }
)
_TARGET_FIELDS = frozenset({"geometry_index", "endpoint", "target_point_mm"})


@dataclass(frozen=True, slots=True)
class SketchExtendSpec(SketchCurvePointSpec):
    endpoint: str


def prepare_sketch_extend_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchExtendSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    raw_target = value["target"]
    if not isinstance(raw_target, Mapping) or set(raw_target) != _TARGET_FIELDS:
        raise NativeSketchError(f"A {LABEL} target has incorrect fields.")
    endpoint = raw_target["endpoint"]
    if endpoint not in {"start", "end"}:
        raise NativeSketchError(f"{LABEL} endpoint must be 'start' or 'end'.")

    shared = prepare_sketch_curve_point_target(
        document_uid,
        {
            "sketch": value["sketch"],
            "expected_geometry_count": value["expected_geometry_count"],
            "expected_constraint_count": value["expected_constraint_count"],
            "expected_external_geometry_count": value[
                "expected_external_geometry_count"
            ],
            "target": {
                "geometry_index": raw_target["geometry_index"],
                "reference_point_mm": raw_target["target_point_mm"],
            },
        },
        label=LABEL,
    )
    return SketchExtendSpec(
        shared.target,
        shared.expected_external_geometry_count,
        shared.selection,
        endpoint,
    )
