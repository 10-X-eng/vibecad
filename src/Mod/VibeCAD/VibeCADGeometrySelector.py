# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact-cardinality geometric selectors shared by CAD domains."""

from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any


_ELEMENT = re.compile(r"^(Face|Edge)([1-9][0-9]*)$")


class GeometrySelectorError(RuntimeError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        self.details = dict(details or {})
        super().__init__(message)


def subshape_geometry(
    shape: Any,
    kind: str,
    index: int,
    subshape: Any,
) -> dict[str, Any]:
    center = getattr(subshape, "CenterOfMass", None)
    geometry = None
    try:
        geometry = getattr(subshape, "Surface" if kind == "face" else "Curve")
    except Exception:
        pass
    result: dict[str, Any] = {
        "name": f"{kind.title()}{index}",
        "element_type": kind,
        "geometry_type": (
            type(geometry).__name__.removeprefix("Part.")
            if geometry is not None
            else "Undefined"
        ),
        "center_mm": (
            [float(center.x), float(center.y), float(center.z)]
            if center is not None
            else None
        ),
    }
    if kind == "face":
        result["area_mm2"] = float(subshape.Area)
        try:
            u_min, u_max, v_min, v_max = (
                float(value) for value in subshape.ParameterRange
            )
            normal = subshape.normalAt(
                (u_min + u_max) / 2.0,
                (v_min + v_max) / 2.0,
            )
            result["normal"] = [float(normal.x), float(normal.y), float(normal.z)]
        except Exception:
            result["normal"] = None
    else:
        result["length_mm"] = float(subshape.Length)
        try:
            first, last = (float(value) for value in subshape.ParameterRange)
            tangent = subshape.tangentAt((first + last) / 2.0)
            result["direction"] = [
                float(tangent.x),
                float(tangent.y),
                float(tangent.z),
            ]
        except Exception:
            result["direction"] = None
    radius = getattr(geometry, "Radius", None)
    if radius is not None:
        result["radius_mm"] = float(radius)
    return result


def _unit(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    length = math.sqrt(sum(float(item) ** 2 for item in value))
    if length <= 1.0e-12:
        return None
    return [float(item) / length for item in value]


def _angle_matches(actual: Any, requested: Any, tolerance: float) -> bool:
    left = _unit(actual)
    right = _unit(requested)
    if left is None or right is None:
        return False
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))
    return math.degrees(math.acos(dot)) <= tolerance


def _geometry_details(shape: Any, kind: str) -> list[dict[str, Any]]:
    values = list(shape.Faces if kind == "face" else shape.Edges)
    return [
        subshape_geometry(shape, kind, index, value)
        for index, value in enumerate(values, start=1)
    ]


def _matches(item: Mapping[str, Any], selection: Mapping[str, Any]) -> bool:
    geometry_type = str(selection.get("geometry_type") or "")
    if geometry_type and str(item.get("geometry_type") or "").lower() != (
        geometry_type.lower()
    ):
        return False
    if "normal" in selection and not _angle_matches(
        item.get("normal"),
        selection["normal"],
        float(selection.get("normal_tolerance_degrees", 1.0)),
    ):
        return False
    if "direction" in selection and not _angle_matches(
        item.get("direction"),
        selection["direction"],
        float(selection.get("direction_tolerance_degrees", 1.0)),
    ):
        return False
    if "radius" in selection:
        radius = item.get("radius_mm")
        if radius is None or abs(
            float(radius) - float(selection["radius"])
        ) > float(selection.get("radius_tolerance", 1.0e-6)):
            return False
    area = item.get("area_mm2")
    if "min_area" in selection and (
        area is None or float(area) < float(selection["min_area"])
    ):
        return False
    if "max_area" in selection and (
        area is None or float(area) > float(selection["max_area"])
    ):
        return False
    length = item.get("length_mm")
    if "min_length" in selection and (
        length is None or float(length) < float(selection["min_length"])
    ):
        return False
    if "max_length" in selection and (
        length is None or float(length) > float(selection["max_length"])
    ):
        return False
    if "near_point" in selection:
        center = item.get("center_mm")
        if center is None or math.dist(center, selection["near_point"]) > float(
            selection.get("max_distance", 1.0e-6)
        ):
            return False
    return True


def resolve_geometry_selection(
    shape: Any,
    selection: Mapping[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    mode = str(selection.get("type") or "")
    if mode == "all_edges":
        details = [
            subshape_geometry(shape, "edge", index, edge)
            for index, edge in enumerate(list(shape.Edges), start=1)
        ]
        if not details:
            raise GeometrySelectorError("The selected feature has no edges.")
        return [item["name"] for item in details], details
    kind = str(selection.get("element_type") or "")
    if kind not in {"face", "edge"}:
        raise GeometrySelectorError("A geometric selection requires face or edge.")
    details = _geometry_details(shape, kind)
    selected = [item for item in details if _matches(item, selection)]
    expected = int(selection.get("expected_count") or 0)
    if len(selected) != expected:
        raise GeometrySelectorError(
            "A geometric selection did not match its declared cardinality.",
            details={
                "stage": "topology_selection",
                "selection": dict(selection),
                "expected_count": expected,
                "actual_count": len(selected),
                "matches": selected,
                "available": details[:256],
            },
        )
    return [str(item["name"]) for item in selected], selected


def _selection_attempts(facts: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    selection: dict[str, Any] = {
        "type": "query",
        "element_type": kind,
        "expected_count": 1,
        "geometry_type": str(facts["geometry_type"]),
    }
    if facts.get("radius_mm") is not None:
        selection["radius"] = float(facts["radius_mm"])
    center = facts.get("center_mm")
    if isinstance(center, list) and len(center) == 3:
        selection["near_point"] = [float(value) for value in center]

    attempts = [dict(selection)]
    size_name = "area_mm2" if kind == "face" else "length_mm"
    value = facts.get(size_name)
    if value is not None:
        epsilon = max(1.0e-6, abs(float(value)) * 1.0e-9)
        sized = dict(selection)
        prefix = "area" if kind == "face" else "length"
        sized[f"min_{prefix}"] = float(value) - epsilon
        sized[f"max_{prefix}"] = float(value) + epsilon
        attempts.append(sized)
    direction_name = "normal" if kind == "face" else "direction"
    direction = facts.get(direction_name)
    if isinstance(direction, list) and len(direction) == 3:
        oriented = dict(attempts[-1])
        oriented[direction_name] = [float(value) for value in direction]
        oriented[f"{direction_name}_tolerance_degrees"] = 1.0e-4
        attempts.append(oriented)

    return attempts


def selections_for_subelements(
    shape: Any,
    element_names: list[str] | tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    """Return shortest exact queries for several elements with one topology scan."""

    parsed: dict[str, tuple[str, int]] = {}
    for raw_name in element_names:
        name = str(raw_name or "")
        match = _ELEMENT.fullmatch(name)
        if match is not None:
            parsed[name] = (match.group(1).lower(), int(match.group(2)))
    details_by_kind = {
        kind: _geometry_details(shape, kind)
        for kind in {value[0] for value in parsed.values()}
    }
    result: dict[str, dict[str, Any]] = {}
    for name, (kind, index) in parsed.items():
        details = details_by_kind[kind]
        if index > len(details):
            continue
        facts = details[index - 1]
        for candidate in _selection_attempts(facts, kind):
            selected = [
                str(item["name"])
                for item in details
                if _matches(item, candidate)
            ]
            if selected == [name]:
                result[name] = candidate
                break
    return result


def selection_for_subelement(shape: Any, element_name: str) -> dict[str, Any] | None:
    """Return the shortest exact query that uniquely reselects one face or edge."""

    return selections_for_subelements(shape, [element_name]).get(str(element_name))
