# SPDX-License-Identifier: LGPL-2.1-or-later

"""Fail-closed semantic inference for the general Drawing Dimension action."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeDrawingDimension import (
    PreparedDrawingDimension,
    prepare_drawing_dimension,
)
from VibeCADNativeDrawingDimensionSupport import (
    drawing_dimension_error,
    prepare_drawing_dimension_target,
)


def _candidate(operation: str, reason: str, elements: tuple[str, ...]) -> dict[str, Any]:
    return {
        "capability": (
            "drawing.dimension_series"
            if operation.endswith(("_chain", "_coordinate"))
            else "drawing.dimension"
        ),
        "operation": operation,
        "reason": reason,
        "subelements": list(elements),
    }


def _candidates(states: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
    names = tuple(str(state["name"]) for state in states)
    kinds = tuple(str(state["element_type"]) for state in states)
    if len(states) == 1 and kinds == ("face",):
        return [_candidate("create_area", "one exact projected face", names)]
    if all(kind == "face" for kind in kinds):
        return [
            _candidate("create_area", "choose the exact face to measure", (name,))
            for name in names
        ]
    if all(kind == "vertex" for kind in kinds):
        if len(states) == 1:
            return []
        result = [
            _candidate("create_length", "aligned distance", names[:2]),
            _candidate("create_horizontal", "horizontal distance", names[:2]),
            _candidate("create_vertical", "vertical distance", names[:2]),
        ]
        if len(states) >= 3:
            result.extend(
                _candidate(
                    f"create_{direction}_{kind}",
                    f"{direction} {kind} series",
                    names,
                )
                for kind in ("chain", "coordinate")
                for direction in ("horizontal", "vertical", "oblique")
            )
        if len(states) == 3:
            result.append(
                _candidate(
                    "create_three_point_angle",
                    "three-point angle requires an explicit apex and arm order",
                    names,
                )
            )
        return result
    if len(states) == 1 and kinds == ("edge",):
        edge = states[0]
        geometry = str(edge.get("geometry_type", "") or "").casefold()
        circular = "circle" in geometry or "ellipse" in geometry
        if circular:
            result = [
                _candidate("create_radius", "radial size", names),
                _candidate("create_diameter", "diametral size", names),
            ]
            if not bool(edge.get("closed", False)):
                result.append(
                    _candidate("create_arc_length_dimension", "open-arc length", names)
                )
            return result
        return [
            _candidate("create_length", "aligned edge length", names),
            _candidate("create_horizontal", "horizontal edge extent", names),
            _candidate("create_vertical", "vertical edge extent", names),
        ]
    if all(kind == "edge" for kind in kinds):
        result = [
            _candidate("create_length", "aligned separation", names[:2]),
            _candidate("create_horizontal", "horizontal separation", names[:2]),
            _candidate("create_vertical", "vertical separation", names[:2]),
            _candidate("create_horizontal_extent", "combined horizontal extent", names),
            _candidate("create_vertical_extent", "combined vertical extent", names),
        ]
        if len(states) == 2:
            result.append(_candidate("create_angle", "two-edge angle", names))
        return result
    if len(states) == 2 and set(kinds) <= {"edge", "vertex"}:
        return [
            _candidate("create_length", "mixed projected-reference distance", names),
            _candidate("create_horizontal", "mixed horizontal distance", names),
            _candidate("create_vertical", "mixed vertical distance", names),
        ]
    return []


def prepare_drawing_dimension_inference(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedDrawingDimension:
    raw_elements = values["elements"]
    if not isinstance(raw_elements, (list, tuple)) or not 1 <= len(raw_elements) <= 64:
        drawing_dimension_error(
            "General Drawing dimension inference requires 1 to 64 exact projected elements.",
            "NATIVE_DRAWING_DIMENSION_INFERENCE_PARAMETERS_INVALID",
        )
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=tuple(raw_elements),
        allowed_element_types=frozenset({"edge", "vertex", "face"}),
        family="dimension inference",
        code_prefix="NATIVE_DRAWING_DIMENSION_INFERENCE",
    )
    candidates = _candidates(target.element_states_before)
    if len(candidates) != 1:
        code = (
            "NATIVE_DRAWING_DIMENSION_INFERENCE_UNSUPPORTED"
            if not candidates
            else "NATIVE_DRAWING_DIMENSION_INFERENCE_AMBIGUOUS"
        )
        message = (
            "The selected projected elements do not imply a supported dimension."
            if not candidates
            else "The selected projected elements imply multiple valid dimensions; choose one explicit operation."
        )
        drawing_dimension_error(
            message,
            code,
            repair={
                "candidates": candidates,
                "rule": (
                    "Native never guesses radius versus diameter, aligned versus "
                    "directional distance, chain versus coordinate, or angle ordering."
                ),
            },
        )
    candidate = candidates[0]
    if candidate["operation"] != "create_area":
        drawing_dimension_error(
            "The inferred Drawing dimension has no exact direct implementation.",
            "NATIVE_DRAWING_DIMENSION_INFERENCE_UNSUPPORTED",
            repair={"candidates": candidates},
        )
    return prepare_drawing_dimension(
        document,
        operation="create_area",
        values={
            "label": values["label"],
            "page": values["page"],
            "view": values["view"],
            "label_position_on_page_mm": values["label_position_on_page_mm"],
            "face": raw_elements[0],
        },
    )
