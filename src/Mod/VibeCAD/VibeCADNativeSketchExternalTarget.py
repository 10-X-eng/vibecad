# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target shared by Native Sketch external-geometry actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)
from VibeCADNativeTargets import NativeElementRef, NativeObjectRef, NativeTargetError


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "source",
        "role",
    }
)
_SOURCE_FIELDS = frozenset({"object_name"})
_ELEMENT_SOURCE_FIELDS = frozenset({"object_name", "subelement"})


@dataclass(frozen=True, slots=True)
class SketchExternalSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    source: NativeObjectRef
    subelement: str
    defining: bool


def _count(value: Any, name: str, *, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{label} {name} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def prepare_sketch_external_target(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    label: str,
) -> SketchExternalSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {label} definition has incorrect fields.")
    raw_source = value["source"]
    if not isinstance(raw_source, Mapping) or set(raw_source) not in {
        _SOURCE_FIELDS,
        _ELEMENT_SOURCE_FIELDS,
    }:
        raise NativeSketchError(f"A {label} source has incorrect fields.")
    try:
        source = NativeObjectRef(
            str(document_uid or ""),
            str(raw_source["object_name"] or ""),
        )
        subelement = str(raw_source.get("subelement") or "")
        if subelement:
            NativeElementRef(source, subelement)
    except NativeTargetError as exc:
        raise NativeSketchError(f"A {label} source is not an exact target.") from exc
    role = value["role"]
    if role not in {"defining", "reference"}:
        raise NativeSketchError(f"{label} role must be 'defining' or 'reference'.")
    return SketchExternalSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(
            value["expected_external_reference_count"],
            "external reference count",
            label=label,
        ),
        _count(
            value["expected_external_geometry_count"],
            "external geometry count",
            label=label,
        ),
        source,
        subelement,
        role == "defining",
    )
