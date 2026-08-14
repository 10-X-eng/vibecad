# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong preparation for the human-facing FEM mesher settings."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


@dataclass(frozen=True, slots=True)
class PreparedMesherValues:
    kind: str
    values: dict[str, Any]

    def normalized(self) -> dict[str, Any]:
        return dict(self.values)


def _finite(value: Any, *, field: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number) or number < minimum or number > 1.0e12:
        raise NativeAnalyzeError(f"{field} must be between {minimum} and 1e12.")
    return float(format(number, ".15g"))


def _sizes(raw: Mapping[str, Any]) -> tuple[float, float]:
    maximum = _finite(raw["maximum_size_mm"], field="maximum_size_mm")
    minimum = _finite(raw["minimum_size_mm"], field="minimum_size_mm")
    if maximum > 0.0 and minimum > maximum:
        raise NativeAnalyzeError(
            "minimum_size_mm cannot exceed a nonzero maximum_size_mm."
        )
    return maximum, minimum


def prepare_mesher_values(kind: str, value: Any) -> PreparedMesherValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("settings must be one typed FEM mesher object.")
    raw = dict(value)
    if kind == "gmsh":
        required = {
            "maximum_size_mm",
            "minimum_size_mm",
            "element_dimension",
            "element_order",
        }
        if set(raw) != required:
            raise NativeAnalyzeError(
                "Gmsh settings must contain maximum_size_mm, minimum_size_mm, "
                "element_dimension, and element_order."
            )
        maximum, minimum = _sizes(raw)
        dimension = str(raw["element_dimension"])
        order = str(raw["element_order"])
        if dimension not in {"from_shape", "1d", "2d", "3d"}:
            raise NativeAnalyzeError("element_dimension is not a supported Gmsh dimension.")
        if order not in {"first", "second"}:
            raise NativeAnalyzeError("element_order must be first or second.")
        return PreparedMesherValues(
            kind,
            {
                "maximum_size_mm": maximum,
                "minimum_size_mm": minimum,
                "element_dimension": dimension,
                "element_order": order,
            },
        )
    if kind != "netgen":
        raise NativeAnalyzeError("The requested FEM mesher is unavailable.")
    required = {
        "maximum_size_mm",
        "minimum_size_mm",
        "fineness",
        "second_order",
    }
    optional = {"user_fineness"}
    if not required <= set(raw) or not set(raw) <= required | optional:
        raise NativeAnalyzeError(
            "Netgen settings must contain maximum_size_mm, minimum_size_mm, "
            "fineness, second_order, and only user_fineness when applicable."
        )
    maximum, minimum = _sizes(raw)
    fineness = str(raw["fineness"])
    allowed = {"very_coarse", "coarse", "moderate", "fine", "very_fine", "user_defined"}
    if fineness not in allowed:
        raise NativeAnalyzeError("fineness is not a supported Netgen preset.")
    if type(raw["second_order"]) is not bool:
        raise NativeAnalyzeError("second_order must be true or false.")
    values: dict[str, Any] = {
        "maximum_size_mm": maximum,
        "minimum_size_mm": minimum,
        "fineness": fineness,
        "second_order": raw["second_order"],
    }
    if fineness == "user_defined":
        user = raw.get("user_fineness")
        required_user = {"growth_rate", "curvature_safety", "segments_per_edge"}
        if not isinstance(user, Mapping) or set(user) != required_user:
            raise NativeAnalyzeError(
                "user_defined fineness requires growth_rate, curvature_safety, "
                "and segments_per_edge."
            )
        values["user_fineness"] = {
            name: _finite(user[name], field=f"user_fineness.{name}")
            for name in sorted(required_user)
        }
    elif "user_fineness" in raw:
        raise NativeAnalyzeError("user_fineness is valid only with user_defined fineness.")
    return PreparedMesherValues(kind, values)


def apply_mesher_values(obj: Any, prepared: PreparedMesherValues) -> None:
    if not isinstance(prepared, PreparedMesherValues):
        raise TypeError("prepared must be PreparedMesherValues")
    values = prepared.values
    if prepared.kind == "gmsh":
        obj.CharacteristicLengthMax = f"{values['maximum_size_mm']} mm"
        obj.CharacteristicLengthMin = f"{values['minimum_size_mm']} mm"
        obj.ElementDimension = {
            "from_shape": "From Shape",
            "1d": "1D",
            "2d": "2D",
            "3d": "3D",
        }[values["element_dimension"]]
        obj.ElementOrder = {"first": "1st", "second": "2nd"}[
            values["element_order"]
        ]
        obj.SecondOrderLinear = False
        return
    obj.MaxSize = f"{values['maximum_size_mm']} mm"
    obj.MinSize = f"{values['minimum_size_mm']} mm"
    obj.Fineness = {
        "very_coarse": "VeryCoarse",
        "coarse": "Coarse",
        "moderate": "Moderate",
        "fine": "Fine",
        "very_fine": "VeryFine",
        "user_defined": "UserDefined",
    }[values["fineness"]]
    obj.SecondOrder = values["second_order"]
    obj.SecondOrderLinear = False
    obj.EndStep = "OptimizeVolume"
    if values["fineness"] == "user_defined":
        user = values["user_fineness"]
        obj.GrowthRate = user["growth_rate"]
        obj.CurvatureSafety = user["curvature_safety"]
        obj.SegmentsPerEdge = user["segments_per_edge"]
