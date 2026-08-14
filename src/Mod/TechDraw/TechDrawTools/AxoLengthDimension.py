# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact implementation for TechDraw axonometric length dimensions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import FreeCAD as App
import TechDrawGui


_ANGULAR_TOLERANCE_RADIANS = 0.01
_AXIS_PARALLEL_TOLERANCE_RADIANS = 0.1
_MINIMUM_DIRECTION_LENGTH = 1.0e-12


class ParallelAxonometricDirectionsError(ValueError):
    """The two direction edges cannot define an axonometric dimension."""


@dataclass(frozen=True, slots=True)
class AxonometricLengthAnalysis:
    line_angle_degrees: float
    extension_angle_degrees: float
    value_mode: str


@dataclass(frozen=True, slots=True)
class AxonometricLengthResult:
    dimension: object
    analysis: AxonometricLengthAnalysis
    projected_value_mm: float
    displayed_value_mm: float


def _make_plumb(angle_radians: float) -> float:
    half_pi = math.pi / 2.0
    if math.isclose(
        angle_radians,
        half_pi,
        abs_tol=_ANGULAR_TOLERANCE_RADIANS,
    ):
        return half_pi
    if math.isclose(
        angle_radians,
        -half_pi,
        abs_tol=_ANGULAR_TOLERANCE_RADIANS,
    ):
        return -half_pi
    return angle_radians


def _edge_vector(view, edge_name: str, noun: str):
    if not isinstance(edge_name, str) or not edge_name.startswith("Edge"):
        raise ValueError(f"The {noun} must be one projected EdgeN reference.")
    try:
        edge = view.getEdgeBySelection(edge_name)
    except Exception as exc:
        raise ValueError(f"The {noun} does not exist in the projected view.") from exc
    vertices = tuple(getattr(edge, "Vertexes", ()) or ())
    if len(vertices) != 2:
        raise ValueError(f"The {noun} must be a straight open projected edge.")
    vector = vertices[1].Point.sub(vertices[0].Point)
    if float(vector.Length) <= _MINIMUM_DIRECTION_LENGTH:
        raise ValueError(f"The {noun} has no usable direction.")
    return vector


def _angle_degrees(vector, *, plumb: bool) -> float:
    angle = vector.getAngle(App.Vector(1.0, 0.0, 0.0))
    if plumb:
        angle = _make_plumb(angle)
    result = math.degrees(angle)
    if float(vector.y) < 0.0:
        result = 180.0 - result
    return float(result)


def _coordinate_vectors(view):
    direction = App.Vector(view.Direction)
    if float(direction.Length) <= _MINIMUM_DIRECTION_LENGTH:
        raise ValueError("The drawing view direction has no usable length.")
    direction = direction / float(direction.Length)
    x_direction = App.Vector(view.XDirection)
    x_direction = x_direction - direction * x_direction.dot(direction)
    if float(x_direction.Length) <= _MINIMUM_DIRECTION_LENGTH:
        raise ValueError(
            "The drawing view X direction is parallel to its projection direction."
        )
    x_direction = x_direction / float(x_direction.Length)
    y_direction = direction.cross(x_direction)
    if float(y_direction.Length) <= _MINIMUM_DIRECTION_LENGTH:
        raise ValueError("The drawing view cannot define an in-plane Y direction.")
    y_direction = y_direction / float(y_direction.Length)
    return tuple(
        App.Vector(axis.dot(x_direction), axis.dot(y_direction), 0.0)
        for axis in (
            App.Vector(1.0, 0.0, 0.0),
            App.Vector(0.0, 1.0, 0.0),
            App.Vector(0.0, 0.0, 1.0),
        )
    )


def _axis_value_mode(view, dimension_vector) -> str:
    coordinate_vectors = _coordinate_vectors(view)
    names = ("x_axis_true_length", "y_axis_true_length", "z_axis_true_length")
    matches = [
        name
        for name, vector in zip(names, coordinate_vectors)
        if float(vector.Length) > _MINIMUM_DIRECTION_LENGTH
        and vector.isParallel(
            dimension_vector,
            _AXIS_PARALLEL_TOLERANCE_RADIANS,
        )
    ]
    if len(matches) > 1:
        raise ValueError(
            "The axonometric dimension direction is ambiguous between projected axes."
        )
    return matches[0] if matches else "projected"


def axonometric_value_mode(view, dimension_direction_edge: str) -> str:
    """Classify one exact direction edge without changing the document."""

    vector = _edge_vector(
        view,
        dimension_direction_edge,
        "dimension-direction edge",
    )
    return _axis_value_mode(view, vector)


def analyze_axonometric_length(
    view,
    measurement_references: Sequence[str],
    dimension_direction_edge: str,
    extension_direction_edge: str,
) -> AxonometricLengthAnalysis:
    """Validate exact projected inputs and classify the displayed value mode."""

    if view is None or not view.isDerivedFrom("TechDraw::DrawViewPart"):
        raise TypeError("view must be a TechDraw::DrawViewPart")
    if view.Document is None or view.findParentPage() is None:
        raise ValueError("The axonometric dimension view is not attached to a page.")
    references = tuple(measurement_references)
    if len(references) not in {1, 2}:
        raise ValueError(
            "Axonometric length requires one projected edge or two projected vertices."
        )
    if len(set(references)) != len(references):
        raise ValueError("Axonometric measurement references must be unique.")
    if len(references) == 1 and not references[0].startswith("Edge"):
        raise ValueError("A one-reference axonometric measurement requires an EdgeN.")
    if len(references) == 2 and not all(
        name.startswith("Vertex") for name in references
    ):
        raise ValueError("A two-reference axonometric measurement requires two VertexN values.")
    if dimension_direction_edge == extension_direction_edge:
        raise ValueError(
            "Dimension and extension directions require two distinct projected edges."
        )
    TechDrawGui.validateProjectedDimension(
        view,
        "Distance",
        list(references),
        False,
    )
    dimension_vector = _edge_vector(
        view,
        dimension_direction_edge,
        "dimension-direction edge",
    )
    extension_vector = _edge_vector(
        view,
        extension_direction_edge,
        "extension-direction edge",
    )
    line_angle = _angle_degrees(dimension_vector, plumb=True)
    extension_angle = _angle_degrees(extension_vector, plumb=False)
    if math.isclose(line_angle, extension_angle, abs_tol=0.1):
        raise ParallelAxonometricDirectionsError(
            "Dimension and extension directions are parallel; choose a distinct orientation."
        )
    return AxonometricLengthAnalysis(
        line_angle_degrees=line_angle,
        extension_angle_degrees=extension_angle,
        value_mode=_axis_value_mode(view, dimension_vector),
    )


def _format_value_to_spec(value: float, format_spec: str) -> str:
    converted = "{" + str(format_spec).replace("%", ":") + "}"
    if "w" in converted:
        converted = converted.replace("w", "f")
        marker = converted.find(":.")
        if marker >= 0:
            digits = int(converted[marker + 2])
            rendered = list(converted.format(round(value, digits)))
            while rendered and rendered[-1] == "0":
                rendered.pop()
            if rendered and rendered[-1] == ".":
                rendered.pop()
            return "".join(rendered)
    return converted.format(value)


def _axis_vector(view, value_mode: str):
    index = {
        "x_axis_true_length": 0,
        "y_axis_true_length": 1,
        "z_axis_true_length": 2,
    }.get(value_mode)
    return None if index is None else _coordinate_vectors(view)[index]


def create_axonometric_length(
    view,
    measurement_references: Sequence[str],
    dimension_direction_edge: str,
    extension_direction_edge: str,
    *,
    label_position_in_view_mm: tuple[float, float] | None,
) -> AxonometricLengthResult:
    """Create one dimension without opening or committing a transaction."""

    analysis = analyze_axonometric_length(
        view,
        measurement_references,
        dimension_direction_edge,
        extension_direction_edge,
    )
    initial_position = label_position_in_view_mm or (0.0, 0.0)
    dimension = TechDrawGui.createProjectedDimension(
        view,
        "Distance",
        list(measurement_references),
        False,
        float(initial_position[0]),
        float(initial_position[1]),
    )
    dimension.AngleOverride = True
    dimension.LineAngle = analysis.line_angle_degrees
    dimension.ExtensionAngle = analysis.extension_angle_degrees
    dimension.recompute()

    if label_position_in_view_mm is None:
        first, second = dimension.getLinearPoints()
        midpoint = (first + second) / 2.0
        dimension.X = midpoint.x
        dimension.Y = -midpoint.y

    arrow_tips = dimension.getArrowPositions()
    if len(arrow_tips) != 2:
        raise RuntimeError("The axonometric dimension did not produce two arrow positions.")
    projected_value = float(arrow_tips[1].sub(arrow_tips[0]).Length)
    displayed_value = projected_value
    axis_vector = _axis_vector(view, analysis.value_mode)
    if axis_vector is not None:
        axis_length = float(axis_vector.Length)
        if axis_length <= _MINIMUM_DIRECTION_LENGTH:
            raise RuntimeError("The selected projected axis has no measurable scale.")
        displayed_value = projected_value / axis_length
        dimension.Arbitrary = True
        dimension.FormatSpec = _format_value_to_spec(
            displayed_value,
            str(dimension.FormatSpec),
        )
    dimension.recompute()
    view.touch()
    return AxonometricLengthResult(
        dimension=dimension,
        analysis=analysis,
        projected_value_mm=projected_value,
        displayed_value_mm=displayed_value,
    )


def repair_axonometric_length(
    dimension,
    view,
    measurement_references: Sequence[str],
    dimension_direction_edge: str,
    extension_direction_edge: str,
) -> AxonometricLengthResult:
    """Replace exact references on one axonometric dimension in its transaction."""

    if (
        dimension is None
        or not dimension.isDerivedFrom("TechDraw::DrawViewDimension")
        or dimension.Document is None
        or dimension.findParentPage() is not view.findParentPage()
        or str(dimension.Type) != "Distance"
        or not bool(dimension.AngleOverride)
    ):
        raise ValueError(
            "The repair target is not an axonometric length on the exact drawing page."
        )
    analysis = analyze_axonometric_length(
        view,
        measurement_references,
        dimension_direction_edge,
        extension_direction_edge,
    )
    prior_format = str(dimension.FormatSpec)
    prior_arbitrary = bool(dimension.Arbitrary)
    default_format = str(TechDrawGui.defaultDimensionFormatSpec(dimension))
    TechDrawGui.repairProjectedDimension(
        dimension,
        view,
        list(measurement_references),
        False,
    )
    dimension.AngleOverride = True
    dimension.LineAngle = analysis.line_angle_degrees
    dimension.ExtensionAngle = analysis.extension_angle_degrees
    dimension.Arbitrary = False
    dimension.FormatSpec = default_format if prior_arbitrary else prior_format
    dimension.recompute()

    arrow_tips = dimension.getArrowPositions()
    if len(arrow_tips) != 2:
        raise RuntimeError(
            "The repaired axonometric dimension did not produce two arrow positions."
        )
    projected_value = float(arrow_tips[1].sub(arrow_tips[0]).Length)
    displayed_value = projected_value
    axis_vector = _axis_vector(view, analysis.value_mode)
    if axis_vector is not None:
        axis_length = float(axis_vector.Length)
        if axis_length <= _MINIMUM_DIRECTION_LENGTH:
            raise RuntimeError("The selected projected axis has no measurable scale.")
        displayed_value = projected_value / axis_length
        dimension.Arbitrary = True
        dimension.FormatSpec = _format_value_to_spec(
            displayed_value,
            default_format,
        )
    dimension.recompute()
    if not dimension.isValid():
        raise RuntimeError("The repaired axonometric dimension is invalid.")
    return AxonometricLengthResult(
        dimension=dimension,
        analysis=analysis,
        projected_value_mm=projected_value,
        displayed_value_mm=displayed_value,
    )
