# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact element targets for non-mutating Sketch relationship reads."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeSketchConstraintTargets import sketch_constraint_geometry
from VibeCADNativeSketchErrors import NativeSketchError


POSITION_CODES = {"whole": 0, "start": 1, "end": 2, "center": 3}
POSITION_NAMES = {code: name for name, code in POSITION_CODES.items()}
_ELEMENT_FIELDS = frozenset({"geometry_index", "position"})


@dataclass(frozen=True, slots=True)
class SketchInspectElement:
    geometry_index: int
    position: str

    @property
    def position_code(self) -> int:
        return POSITION_CODES[self.position]

    def summary(self) -> dict[str, Any]:
        return {
            "geometry_index": self.geometry_index,
            "position": self.position,
        }


def bounded_sketch_count(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(f"Sketch {label} must be an integer from 0 to 1000000.")
    return value


def parse_sketch_inspect_element(value: Any) -> SketchInspectElement:
    if not isinstance(value, Mapping) or set(value) != _ELEMENT_FIELDS:
        raise NativeSketchError("A Sketch inspect element has incorrect fields.")
    index = value["geometry_index"]
    if (
        type(index) is not int
        or not (0 <= index < 1_000_000 or -1_000_000 <= index <= -1)
        or index == -2000
    ):
        raise NativeSketchError(
            "A Sketch inspect geometry_index must name geometry, an axis, or external geometry."
        )
    position = value["position"]
    if not isinstance(position, str) or position not in POSITION_CODES:
        raise NativeSketchError(
            "A Sketch inspect position must be whole, start, end, or center."
        )
    return SketchInspectElement(index, position)


def relationship_element(index: int, position_code: int) -> SketchInspectElement:
    position = POSITION_NAMES.get(position_code)
    if position is None:
        raise NativeSketchError(
            "A Sketch constraint has an unsupported point position."
        )
    return parse_sketch_inspect_element({"geometry_index": index, "position": position})


def _external_record(
    external_records: tuple[str, ...],
    index: int,
) -> dict[str, Any] | None:
    for encoded in external_records:
        record = json.loads(encoded)
        if int(record["geometry_index"]) == index:
            return record
    return None


def validate_sketch_inspect_element(
    sketch: Any,
    element: SketchInspectElement,
    *,
    expected_geometry_count: int,
    expected_external_geometry_count: int,
    external_records: tuple[str, ...],
) -> None:
    index = element.geometry_index
    if index >= expected_geometry_count:
        raise NativeSketchError(f"Sketch geometry {index} is unavailable.")
    if index <= -3 and -index - 3 >= expected_external_geometry_count:
        raise NativeSketchError(f"Sketch external geometry {index} is unavailable.")
    if index <= -3:
        external = _external_record(external_records, index)
        if external is None:
            raise NativeSketchError(f"Sketch external geometry {index} is unavailable.")
        if any(
            external.get(flag) is True
            for flag in ("missing", "detached", "synchronized")
        ):
            raise NativeSketchError(
                f"Sketch external geometry {index} is not a stable exact selection target."
            )
    sketch_constraint_geometry(sketch, index)
    if element.position == "whole":
        return
    if index == -2 or (index == -1 and element.position != "start"):
        raise NativeSketchError("The selected Sketch axis does not expose that point.")
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError("Sketch point lookup is unavailable.")
    try:
        getter(index, element.position_code)
    except Exception as exc:
        raise NativeSketchError(
            f"Sketch geometry {index} does not expose its {element.position} point."
        ) from exc
