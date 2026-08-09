# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact proof for conic internal geometry and alignments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import same_sketch_point


@dataclass(frozen=True, slots=True)
class ExpectedInternalGeometry:
    role: str
    kind: str
    first_mm: tuple[float, float]
    second_mm: tuple[float, float] | None = None


def exposed_internal_indices(
    raw: Any,
    *,
    source_index: int,
    roles: tuple[str, ...],
    label: str,
) -> tuple[int, ...]:
    if not isinstance(raw, Mapping):
        raise NativeSketchError(f"Sketcher did not report exposed {label} geometry.")
    expected = tuple(range(source_index + 1, source_index + 1 + len(roles)))
    created = raw.get("created")
    try:
        indices = tuple(int(item["geometry_index"]) for item in created)
        reported_roles = tuple(str(item["role"]) for item in created)
        reported_source = int(raw["source_geometry_index"])
        count_before = int(raw["geometry_count_before"])
        count_after = int(raw["geometry_count_after"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NativeSketchError(
            f"Sketcher returned malformed exposed {label} geometry."
        ) from exc
    if (
        reported_source != source_index
        or count_before != source_index + 1
        or count_after != source_index + 1 + len(roles)
        or indices != expected
        or reported_roles != roles
    ):
        raise NativeSketchError(f"Sketcher exposed unexpected {label} geometry.")
    return indices


def verify_internal_geometry_records(
    records: Sequence[Mapping[str, Any]],
    expected: tuple[ExpectedInternalGeometry, ...],
    *,
    label: str,
) -> None:
    for record, definition in zip(records, expected, strict=True):
        if (
            record.get("internal_type") != definition.role
            or record.get("kind") != definition.kind
            or record.get("construction") is not True
            or bool(record.get("blocked"))
        ):
            raise NativeSketchError(f"Sketch {label} internal geometry changed.")
        key = "start_mm" if definition.kind == "line" else "position_mm"
        if not same_sketch_point(record.get(key), definition.first_mm):
            raise NativeSketchError(f"Sketch {label} internal geometry changed.")
        if definition.second_mm is not None and not same_sketch_point(
            record.get("end_mm"),
            definition.second_mm,
        ):
            raise NativeSketchError(f"Sketch {label} internal geometry changed.")


def verify_internal_alignment_records(
    records: Sequence[Mapping[str, Any]],
    expected: tuple[ExpectedInternalGeometry, ...],
    *,
    geometry_index: int,
    internal_indices: tuple[int, ...],
    label: str,
) -> None:
    for record, definition, internal_index in zip(
        records,
        expected,
        internal_indices,
        strict=True,
    ):
        expected_first = {"slot": 1, "geometry_index": internal_index}
        if definition.kind == "point":
            expected_first["position"] = 1
        if (
            record.get("type") != "InternalAlignment"
            or record.get("driving") is not True
            or record.get("active") is not True
            or record.get("virtual") is not False
            or record.get("references")
            != [
                expected_first,
                {"slot": 2, "geometry_index": geometry_index},
            ]
        ):
            raise NativeSketchError(f"Sketch {label} internal alignment changed.")
