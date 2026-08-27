# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact quality expectations for live Mesh acceptance runs."""

from __future__ import annotations

from typing import Any, Mapping


_EXPECTATION_FIELDS = {
    "object_count",
    "all_solid",
    "all_watertight",
    "maximum_issue_counts",
}


def validate_mesh_quality(
    objects: list[Mapping[str, Any]],
    expectations: Mapping[str, Any],
) -> dict[str, Any]:
    unknown = set(expectations) - _EXPECTATION_FIELDS
    if unknown:
        raise ValueError("Unknown Mesh acceptance expectations: " + ", ".join(sorted(unknown)))
    records = [dict(record) for record in objects]
    totals: dict[str, int] = {}
    for record in records:
        for name, count in dict(record.get("issue_counts") or {}).items():
            totals[str(name)] = totals.get(str(name), 0) + int(count)
    evidence = {
        "object_count": len(records),
        "point_count": sum(int(record["points"]) for record in records),
        "facet_count": sum(int(record["facets"]) for record in records),
        "solid_count": sum(bool(record["solid"]) for record in records),
        "watertight_count": sum(bool(record["watertight"]) for record in records),
        "issue_counts": {name: count for name, count in sorted(totals.items()) if count},
        "objects": records,
    }
    expected_count = expectations.get("object_count")
    if expected_count is not None and evidence["object_count"] != expected_count:
        raise AssertionError(
            "Mesh acceptance object_count mismatch: "
            f"expected {expected_count}, found {evidence['object_count']}."
        )
    for field, count_field in (
        ("all_solid", "solid_count"),
        ("all_watertight", "watertight_count"),
    ):
        expected = expectations.get(field)
        if expected is None:
            continue
        if type(expected) is not bool:
            raise ValueError(f"Mesh acceptance {field} must be boolean.")
        actual = evidence[count_field] == evidence["object_count"]
        if actual is not expected:
            raise AssertionError(
                f"Mesh acceptance {field} mismatch: expected {expected}, found {actual}."
            )
    maximums = expectations.get("maximum_issue_counts", {})
    if not isinstance(maximums, Mapping):
        raise ValueError("Mesh acceptance maximum_issue_counts must be an object.")
    for name, maximum in maximums.items():
        if type(maximum) is not int or maximum < 0:
            raise ValueError("Mesh acceptance issue maxima must be non-negative integers.")
        actual = totals.get(str(name), 0)
        if actual > maximum:
            raise AssertionError(
                f"Mesh acceptance {name} exceeds its maximum: "
                f"expected <= {maximum}, found {actual}."
            )
    return evidence
