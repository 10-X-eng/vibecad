# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed values for Gmsh refinement-field composition."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


MANIPULATION_KINDS = (
    "restrict",
    "threshold",
    "mean",
    "gradient",
    "curvature",
    "laplacian",
)
ADVANCED_KINDS = (
    "attractor_aniso_curve",
    "math_eval",
    "math_eval_aniso",
    "distance",
    "result",
)
FIELD_KINDS = (*MANIPULATION_KINDS, *ADVANCED_KINDS)
_FIELD_REFERENCE = re.compile(r"F([0-9]+)")


@dataclass(frozen=True, slots=True)
class PreparedMeshFieldValues:
    kind: str
    values: dict[str, Any]

    @property
    def family(self) -> str:
        return "manipulate" if self.kind in MANIPULATION_KINDS else "advanced"

    def normalized(self) -> dict[str, Any]:
        return dict(self.values)


def _exact(value: Any, names: set[str], *, kind: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != names:
        raise NativeAnalyzeError(
            f"{kind} definition must contain only {', '.join(sorted(names))}."
        )
    return dict(value)


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite positive number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite positive number.") from exc
    if not math.isfinite(number) or number <= 0.0 or number > 1.0e12:
        raise NativeAnalyzeError(f"{field} must be greater than zero and at most 1e12.")
    return float(format(number, ".15g"))


def _integer(value: Any, *, field: str, maximum: int = 100000) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise NativeAnalyzeError(f"{field} must be an integer from 1 to {maximum}.")
    return value


def _bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise NativeAnalyzeError(f"{field} must be true or false.")
    return value


def _equation(value: Any, *, field: str, input_count: int) -> str:
    if not isinstance(value, str):
        raise NativeAnalyzeError(f"{field} must be a Gmsh expression string.")
    expression = value.strip()
    if not expression or len(expression) > 2048:
        raise NativeAnalyzeError(f"{field} must contain 1 to 2048 visible characters.")
    if any(character in expression for character in ("\x00", "\r", "\n", "'", '"', ";", "\\")):
        raise NativeAnalyzeError(
            f"{field} cannot contain quotes, semicolons, backslashes, or line breaks."
        )
    references = tuple(int(match.group(1)) for match in _FIELD_REFERENCE.finditer(expression))
    invalid = tuple(value for value in references if value < 1 or value > input_count)
    if invalid:
        available = "none" if input_count == 0 else f"F1 through F{input_count}"
        raise NativeAnalyzeError(
            f"{field} references unavailable field F{invalid[0]}; available inputs are {available}."
        )
    return expression


def prepare_mesh_field_values(
    kind: str,
    value: Any,
    *,
    input_count: int,
) -> PreparedMeshFieldValues:
    if kind not in FIELD_KINDS:
        raise NativeAnalyzeError("The requested Gmsh refinement field is unavailable.")
    if type(input_count) is not int or not 0 <= input_count <= 8:
        raise NativeAnalyzeError("input refinements must contain at most eight fields.")
    if kind == "restrict":
        raw = _exact(value, {"include_boundary"}, kind=kind)
        values = {
            "include_boundary": _bool(raw["include_boundary"], field="include_boundary")
        }
    elif kind == "threshold":
        names = {
            "input_minimum_mm",
            "input_maximum_mm",
            "size_minimum_mm",
            "size_maximum_mm",
            "linear_interpolation",
            "stop_at_input_maximum",
        }
        raw = _exact(value, names, kind=kind)
        values = {
            name: _number(raw[name], field=name)
            for name in names
            if name.endswith("_mm")
        }
        if values["input_minimum_mm"] > values["input_maximum_mm"]:
            raise NativeAnalyzeError("input_minimum_mm cannot exceed input_maximum_mm.")
        if values["size_minimum_mm"] > values["size_maximum_mm"]:
            raise NativeAnalyzeError("size_minimum_mm cannot exceed size_maximum_mm.")
        values["linear_interpolation"] = _bool(
            raw["linear_interpolation"], field="linear_interpolation"
        )
        values["stop_at_input_maximum"] = _bool(
            raw["stop_at_input_maximum"], field="stop_at_input_maximum"
        )
    elif kind in {"mean", "curvature", "laplacian"}:
        raw = _exact(value, {"delta_mm"}, kind=kind)
        values = {"delta_mm": _number(raw["delta_mm"], field="delta_mm")}
    elif kind == "gradient":
        raw = _exact(value, {"delta_mm", "component"}, kind=kind)
        component = str(raw["component"])
        if component not in {"x", "y", "z", "mean"}:
            raise NativeAnalyzeError("component must be x, y, z, or mean.")
        values = {
            "delta_mm": _number(raw["delta_mm"], field="delta_mm"),
            "component": component,
        }
    elif kind == "attractor_aniso_curve":
        names = {
            "distance_minimum_mm",
            "distance_maximum_mm",
            "size_minimum_normal_mm",
            "size_maximum_normal_mm",
            "size_minimum_tangent_mm",
            "size_maximum_tangent_mm",
            "sampling",
        }
        raw = _exact(value, names, kind=kind)
        values = {
            name: _number(raw[name], field=name)
            for name in names
            if name.endswith("_mm")
        }
        for minimum, maximum in (
            ("distance_minimum_mm", "distance_maximum_mm"),
            ("size_minimum_normal_mm", "size_maximum_normal_mm"),
            ("size_minimum_tangent_mm", "size_maximum_tangent_mm"),
        ):
            if values[minimum] > values[maximum]:
                raise NativeAnalyzeError(f"{minimum} cannot exceed {maximum}.")
        values["sampling"] = _integer(raw["sampling"], field="sampling", maximum=1000)
    elif kind == "distance":
        raw = _exact(value, {"sampling"}, kind=kind)
        values = {
            "sampling": _integer(raw["sampling"], field="sampling", maximum=1000)
        }
    elif kind == "math_eval":
        raw = _exact(value, {"equation"}, kind=kind)
        values = {
            "equation": _equation(
                raw["equation"], field="equation", input_count=input_count
            )
        }
    elif kind == "math_eval_aniso":
        raw = _exact(value, {"metric"}, kind=kind)
        metric = _exact(
            raw["metric"],
            {"m11", "m12", "m13", "m22", "m23", "m33"},
            kind="math_eval_aniso.metric",
        )
        values = {
            "metric": {
                name: _equation(
                    metric[name],
                    field=f"metric.{name}",
                    input_count=input_count,
                )
                for name in ("m11", "m12", "m13", "m22", "m23", "m33")
            }
        }
    else:
        raw = _exact(value, {"field"}, kind=kind)
        field = raw["field"]
        if not isinstance(field, str) or not field.strip() or len(field.strip()) > 160:
            raise NativeAnalyzeError("field must contain 1 to 160 visible characters.")
        values = {"field": field.strip()}
    return PreparedMeshFieldValues(kind, values)


def apply_mesh_field_values(obj: Any, prepared: PreparedMeshFieldValues) -> None:
    values = prepared.values
    if prepared.family == "manipulate":
        obj.Type = prepared.kind.title()
        if prepared.kind == "restrict":
            obj.IncludeBoundary = values["include_boundary"]
        elif prepared.kind == "threshold":
            for field, native in (
                ("input_minimum_mm", "InputMinimum"),
                ("input_maximum_mm", "InputMaximum"),
                ("size_minimum_mm", "SizeMinimum"),
                ("size_maximum_mm", "SizeMaximum"),
            ):
                setattr(obj, native, f"{values[field]} mm")
            obj.LinearInterpolation = values["linear_interpolation"]
            obj.StopAtInputMax = values["stop_at_input_maximum"]
        elif prepared.kind in {"mean", "curvature", "laplacian"}:
            obj.Delta = f"{values['delta_mm']} mm"
        else:
            obj.Delta = f"{values['delta_mm']} mm"
            obj.Kind = values["component"].title()
        return
    obj.Type = {
        "attractor_aniso_curve": "AttractorAnisoCurve",
        "math_eval": "MathEval",
        "math_eval_aniso": "MathEvalAniso",
        "distance": "Distance",
        "result": "Result",
    }[prepared.kind]
    if prepared.kind == "attractor_aniso_curve":
        for field, native in (
            ("distance_minimum_mm", "DistanceMin"),
            ("distance_maximum_mm", "DistanceMax"),
            ("size_minimum_normal_mm", "SizeMinNormal"),
            ("size_maximum_normal_mm", "SizeMaxNormal"),
            ("size_minimum_tangent_mm", "SizeMinTangent"),
            ("size_maximum_tangent_mm", "SizeMaxTangent"),
        ):
            setattr(obj, native, f"{values[field]} mm")
        obj.Sampling = values["sampling"]
    elif prepared.kind == "distance":
        obj.Sampling = values["sampling"]
    elif prepared.kind == "math_eval":
        obj.Equation = values["equation"]
    elif prepared.kind == "math_eval_aniso":
        for name, expression in values["metric"].items():
            setattr(obj, name.upper(), expression)
    else:
        obj.ResultField = values["field"]
