# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict parsing of the shared host external-geometry diagnostic."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchExternalState import (
    SketchExternalSnapshot,
    external_geometry_groups,
    normalized_external_geometry_record,
)
from VibeCADNativeSketchState import serialize_sketch_external_geometry_value
from VibeCADNativeTargets import object_reference


_FIELDS = frozenset(
    {
        "source_object_name",
        "source_subelement",
        "requested_defining",
        "requested_intersection",
        "reference",
        "type",
        "reference_index",
        "added_reference",
        "defining",
        "external_geometry_count",
        "external_geometry",
        "external_geometry_metadata",
    }
)
_METADATA_FIELDS = frozenset(
    {
        "reference",
        "defining",
        "frozen",
        "detached",
        "missing",
        "synchronized",
    }
)
_REQUIRED_GEOMETRY_FIELDS = {
    "point": frozenset({"position_mm"}),
    "line": frozenset({"start_mm", "end_mm"}),
    "circle": frozenset({"center_mm", "radius_mm"}),
    "circular_arc": frozenset({"center_mm", "radius_mm", "start_mm", "end_mm"}),
    "ellipse": frozenset({"center_mm", "major_radius_mm", "minor_radius_mm"}),
    "elliptical_arc": frozenset(
        {"center_mm", "major_radius_mm", "minor_radius_mm", "start_mm", "end_mm"}
    ),
    "hyperbola": frozenset({"center_mm", "major_radius_mm", "minor_radius_mm"}),
    "hyperbolic_arc": frozenset(
        {"center_mm", "major_radius_mm", "minor_radius_mm", "start_mm", "end_mm"}
    ),
    "parabola": frozenset({"center_mm", "focal_length_mm"}),
    "parabolic_arc": frozenset({"center_mm", "focal_length_mm", "start_mm", "end_mm"}),
    "b_spline": frozenset({"degree", "poles_mm"}),
}


@dataclass(frozen=True, slots=True)
class SketchExternalPlan:
    reference: str
    reference_index: int
    added_reference: bool
    defining: bool
    final_kind: str
    outcome: str
    reference_records: tuple[str, ...]
    affected_geometry_records: tuple[str, ...]
    external_geometry_count: int


def _reference_state(
    snapshot: SketchExternalSnapshot,
    *,
    label: str,
    intersection: bool,
):
    decoded = [json.loads(value) for value in snapshot.external_reference_records]
    matches = [
        record
        for record in decoded
        if record.get("object", {}).get("object_name") == snapshot.source.Name
        and record.get("subelement", "") == snapshot.spec.subelement
    ]
    if len(matches) > 1:
        raise NativeSketchError(f"{label} found duplicate durable source links.")
    existing = matches[0] if matches else None
    complementary_kind = "projection" if intersection else "intersection"
    if existing and existing.get("kind") != complementary_kind:
        action = "intersected" if intersection else "projected"
        raise NativeSketchError(f"That exact {label} source is already {action}.")
    return decoded, existing


def _validate_existing_role(
    snapshot: SketchExternalSnapshot,
    reference: str,
    *,
    label: str,
) -> None:
    records = external_geometry_groups(snapshot.external_geometry_records).get(
        reference
    )
    if not records:
        raise NativeSketchError(
            f"{label} cannot upgrade a missing external-geometry result."
        )
    decoded = [json.loads(value) for value in records]
    if any(
        type(record.get("defining")) is not bool
        or record["defining"] != snapshot.spec.defining
        or any(
            record.get(flag) is not False
            for flag in ("frozen", "detached", "missing", "synchronized")
        )
        for record in decoded
    ):
        raise NativeSketchError(
            f"{label} cannot change or infer the role of an existing external link."
        )


def _expected_references(
    snapshot: SketchExternalSnapshot,
    decoded: list[dict[str, Any]],
    existing: dict[str, Any] | None,
    *,
    intersection: bool,
) -> tuple[tuple[str, ...], int, str, bool]:
    if existing is None:
        index = len(decoded)
        kind = "intersection" if intersection else "projection"
        decoded.append(
            {
                "reference_index": index,
                "object": object_reference(snapshot.source),
                "subelement": snapshot.spec.subelement,
                "kind": kind,
            }
        )
        return canonical_sketch_records(decoded), index, kind, True
    index = int(existing["reference_index"])
    decoded[index] = {**decoded[index], "kind": "projection_and_intersection"}
    return (
        canonical_sketch_records(decoded),
        index,
        "projection_and_intersection",
        False,
    )


def _validate_geometry_record(record: Mapping[str, Any], *, label: str) -> None:
    kind = str(record.get("kind", "") or "")
    required = _REQUIRED_GEOMETRY_FIELDS.get(kind)
    if required is None or not required.issubset(record):
        raise NativeSketchError(
            f"{label} feasibility returned invalid projected geometry."
        )
    if not str(record.get("type_id", "") or "").startswith("Part::Geom"):
        raise NativeSketchError(
            f"{label} feasibility returned an invalid geometry type."
        )
    for radius in ("radius_mm", "major_radius_mm", "minor_radius_mm"):
        if radius in record and float(record[radius]) <= 0.0:
            raise NativeSketchError(f"{label} feasibility returned an invalid radius.")


def parse_external_diagnostic(
    result: Any,
    snapshot: SketchExternalSnapshot,
    *,
    label: str,
    intersection: bool,
) -> SketchExternalPlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{label} feasibility returned incomplete diagnostics.")
    if (
        result["source_object_name"] != snapshot.source.Name
        or result["source_subelement"] != snapshot.spec.subelement
        or result["requested_defining"] is not snapshot.spec.defining
        or result["requested_intersection"] is not intersection
        or result["defining"] is not snapshot.spec.defining
    ):
        raise NativeSketchError(f"{label} feasibility analyzed a different target.")

    decoded_references, existing = _reference_state(
        snapshot,
        label=label,
        intersection=intersection,
    )
    expected_references, reference_index, final_kind, added = _expected_references(
        snapshot,
        decoded_references,
        existing,
        intersection=intersection,
    )
    expected_type = (1 if intersection else 0) if added else 2
    if (
        result["added_reference"] is not added
        or result["type"] != expected_type
        or result["reference_index"] != reference_index
    ):
        raise NativeSketchError(f"{label} feasibility returned the wrong link outcome.")
    reference = str(result["reference"] or "")
    if not reference or len(reference) > 1_024:
        raise NativeSketchError(
            f"{label} feasibility returned an invalid reference key."
        )
    before_groups = external_geometry_groups(snapshot.external_geometry_records)
    if added:
        if reference in before_groups:
            raise NativeSketchError(
                f"{label} feasibility collided with an existing link."
            )
    else:
        _validate_existing_role(snapshot, reference, label=label)

    geometry = result["external_geometry"]
    metadata = result["external_geometry_metadata"]
    count = result["external_geometry_count"]
    if (
        type(count) is not int
        or not 1 <= count <= 1_000_000
        or not isinstance(geometry, list)
        or not isinstance(metadata, list)
        or len(geometry) != count
        or len(metadata) != count
    ):
        raise NativeSketchError(
            f"{label} feasibility returned an invalid geometry count."
        )
    affected = []
    for offset, (value, raw_metadata) in enumerate(
        zip(geometry, metadata, strict=True)
    ):
        if (
            not isinstance(raw_metadata, Mapping)
            or set(raw_metadata) != _METADATA_FIELDS
            or raw_metadata["reference"] != reference
            or raw_metadata["defining"] is not snapshot.spec.defining
            or any(
                raw_metadata[flag] is not False
                for flag in ("frozen", "detached", "missing", "synchronized")
            )
        ):
            raise NativeSketchError(
                f"{label} feasibility returned invalid external metadata."
            )
        try:
            record = serialize_sketch_external_geometry_value(
                value,
                -3 - offset,
                raw_metadata,
            )
        except Exception as exc:
            raise NativeSketchError(
                f"{label} feasibility returned unreadable projected geometry."
            ) from exc
        _validate_geometry_record(record, label=label)
        affected.append(
            normalized_external_geometry_record(
                json.dumps(
                    record,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        )
    old_count = len(before_groups.get(reference, ()))
    final_count = len(snapshot.external_geometry_records) - old_count + len(affected)
    return SketchExternalPlan(
        reference,
        reference_index,
        added,
        snapshot.spec.defining,
        final_kind,
        (
            "added_intersection"
            if added and intersection
            else "added_projection"
            if added
            else "upgraded_projection"
            if intersection
            else "upgraded_intersection"
        ),
        expected_references,
        tuple(affected),
        final_count,
    )
