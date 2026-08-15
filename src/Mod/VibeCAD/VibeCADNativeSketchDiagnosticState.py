# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict decoding of detached Sketch mutation diagnostics."""

from __future__ import annotations

import json
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchState import (
    serialize_sketch_constraint_value,
    serialize_sketch_external_geometry_value,
    serialize_sketch_geometry_value,
)
from VibeCADNativeTargets import NativeObjectRef, object_reference, resolve_object


ISSUE_FIELDS = (
    "conflicting_constraint_indices",
    "redundant_constraint_indices",
    "partially_redundant_constraint_indices",
    "malformed_constraint_indices",
)
EXTERNAL_METADATA_FIELDS = frozenset(
    {
        "reference",
        "defining",
        "frozen",
        "detached",
        "missing",
        "synchronized",
    }
)
EXTERNAL_REFERENCE_FIELDS = frozenset({"object_name", "subelement", "type"})
EXTERNAL_KINDS = {
    0: "projection",
    1: "intersection",
    2: "projection_and_intersection",
}


def bounded_count(value: Any, field: str, *, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(f"{label} feasibility returned invalid {field}.")
    return value


def bounded_sequence(value: Any, field: str, *, label: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 1_000_000:
        raise NativeSketchError(f"{label} feasibility returned invalid {field}.")
    return tuple(value)


def diagnostic_solver_degrees(result: Mapping[str, Any], *, label: str) -> int:
    if type(result.get("accepted")) is not bool or not result["accepted"]:
        raise NativeSketchError(
            f"{label} would introduce a solver issue; nothing changed."
        )
    degrees = bounded_count(
        result.get("degrees_of_freedom"), "degrees of freedom", label=label
    )
    if type(result.get("solver_status")) is not int or result["solver_status"] != 0:
        raise NativeSketchError(
            f"{label} feasibility returned an invalid solver state."
        )
    for field in ISSUE_FIELDS:
        values = bounded_sequence(result.get(field), field, label=label)
        if values or any(type(value) is not int or value < 0 for value in values):
            raise NativeSketchError(
                f"{label} feasibility returned inconsistent solver issues."
            )
    return degrees


def diagnostic_sketch_records(
    result: Mapping[str, Any], *, label: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    geometry_count = bounded_count(
        result.get("geometry_count"), "geometry count", label=label
    )
    constraint_count = bounded_count(
        result.get("constraint_count"), "constraint count", label=label
    )
    geometry = bounded_sequence(result.get("geometry"), "geometry", label=label)
    metadata = bounded_sequence(
        result.get("geometry_metadata"), "geometry metadata", label=label
    )
    constraints = bounded_sequence(
        result.get("constraints"), "constraints", label=label
    )
    if (
        len(geometry) != geometry_count
        or len(metadata) != geometry_count
        or len(constraints) != constraint_count
    ):
        raise NativeSketchError(
            f"{label} feasibility returned inconsistent Sketch counts."
        )
    try:
        return (
            canonical_sketch_records(
                serialize_sketch_geometry_value(value, index, metadata[index])
                for index, value in enumerate(geometry)
            ),
            canonical_sketch_records(
                serialize_sketch_constraint_value(value, index)
                for index, value in enumerate(constraints)
            ),
        )
    except Exception as exc:
        raise NativeSketchError(
            f"{label} feasibility returned unreadable Sketch state."
        ) from exc


def diagnostic_external_reference_records(
    result: Mapping[str, Any],
    document: Any,
    document_uid: str,
    *,
    label: str,
) -> tuple[str, ...]:
    count = bounded_count(
        result.get("external_reference_count"), "external reference count", label=label
    )
    values = bounded_sequence(
        result.get("external_references"), "external references", label=label
    )
    if len(values) != count:
        raise NativeSketchError(
            f"{label} feasibility returned inconsistent external links."
        )
    records = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping) or set(value) != EXTERNAL_REFERENCE_FIELDS:
            raise NativeSketchError(
                f"{label} feasibility returned an invalid external link."
            )
        kind = EXTERNAL_KINDS.get(value["type"]) if type(value["type"]) is int else None
        if (
            not isinstance(value["object_name"], str)
            or not value["object_name"]
            or not isinstance(value["subelement"], str)
            or kind is None
        ):
            raise NativeSketchError(
                f"{label} feasibility returned invalid external-link identity."
            )
        obj = resolve_object(
            document,
            NativeObjectRef(document_uid, value["object_name"]),
        )
        records.append(
            {
                "reference_index": index,
                "object": object_reference(obj),
                "subelement": value["subelement"],
                "kind": kind,
            }
        )
    return canonical_sketch_records(records)


def diagnostic_external_geometry_records(
    result: Mapping[str, Any], *, label: str
) -> tuple[str, ...]:
    count = bounded_count(
        result.get("external_geometry_count"), "external geometry count", label=label
    )
    geometry = bounded_sequence(
        result.get("external_geometry"), "external geometry", label=label
    )
    metadata = bounded_sequence(
        result.get("external_geometry_metadata"),
        "external geometry metadata",
        label=label,
    )
    if len(geometry) != count or len(metadata) != count:
        raise NativeSketchError(
            f"{label} feasibility returned inconsistent external geometry."
        )
    records = []
    for index, (value, item) in enumerate(zip(geometry, metadata, strict=True)):
        if not isinstance(item, Mapping) or set(item) != EXTERNAL_METADATA_FIELDS:
            raise NativeSketchError(
                f"{label} feasibility returned invalid external metadata."
            )
        if any(bool(item[field]) for field in ("detached", "missing", "synchronized")):
            raise NativeSketchError(
                f"{label} feasibility returned unhealthy external geometry."
            )
        try:
            records.append(
                serialize_sketch_external_geometry_value(value, -3 - index, item)
            )
        except Exception as exc:
            raise NativeSketchError(
                f"{label} feasibility returned unreadable external geometry."
            ) from exc
    return canonical_sketch_records(records)


def require_healthy_external_records(records: tuple[str, ...], *, label: str) -> None:
    for encoded in records:
        record = json.loads(encoded)
        if any(
            bool(record.get(field, False))
            for field in ("detached", "missing", "synchronized")
        ):
            raise NativeSketchError(
                f"{label} requires attached, available, synchronized external geometry."
            )
