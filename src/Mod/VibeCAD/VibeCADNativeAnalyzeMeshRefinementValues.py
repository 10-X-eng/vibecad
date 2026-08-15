# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong typed values for the primary FEM mesh refinements."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


MODES = ("region", "group", "distance", "boundary_layer", "shape")
STRUCTURED_MODES = (
    "transfinite_curve",
    "transfinite_surface",
    "transfinite_volume",
)
SUPPORTED_MODES = (*MODES, *STRUCTURED_MODES)


@dataclass(frozen=True, slots=True)
class PreparedRefinementValues:
    mode: str
    values: dict[str, Any]

    def normalized(self) -> dict[str, Any]:
        return dict(self.values)


def _number(
    value: Any,
    *,
    field: str,
    minimum: float = 0.0,
    exclusive: bool = False,
) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    invalid_min = number <= minimum if exclusive else number < minimum
    if not math.isfinite(number) or invalid_min or number > 1.0e12:
        relation = "greater than" if exclusive else "at least"
        raise NativeAnalyzeError(f"{field} must be {relation} {minimum} and at most 1e12.")
    return float(format(number, ".15g"))


def _integer(value: Any, *, field: str, minimum: int = 1, maximum: int = 100000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NativeAnalyzeError(f"{field} must be an integer from {minimum} to {maximum}.")
    return value


def _bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise NativeAnalyzeError(f"{field} must be true or false.")
    return value


def _vector(value: Any, *, field: str, nonzero: bool = False) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeAnalyzeError(f"{field} must contain only x, y, and z.")
    result = {
        axis: _number(value[axis], field=f"{field}.{axis}", minimum=-1.0e12)
        for axis in ("x", "y", "z")
    }
    magnitude = math.sqrt(sum(component * component for component in result.values()))
    if nonzero and magnitude <= 1.0e-12:
        raise NativeAnalyzeError(f"{field} must be a nonzero direction.")
    if nonzero:
        result = {
            axis: float(format(component / magnitude, ".15g"))
            for axis, component in result.items()
        }
    return result


def _exact(value: Any, names: set[str], *, mode: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != names:
        raise NativeAnalyzeError(
            f"{mode} definition must contain only {', '.join(sorted(names))}."
        )
    return dict(value)


def prepare_refinement_values(mode: str, value: Any) -> PreparedRefinementValues:
    if mode not in SUPPORTED_MODES:
        raise NativeAnalyzeError("The requested FEM mesh refinement is unavailable.")
    if mode == "region":
        raw = _exact(value, {"element_size_mm"}, mode=mode)
        values = {
            "element_size_mm": _number(
                raw["element_size_mm"], field="element_size_mm", exclusive=True
            )
        }
    elif mode == "group":
        raw = _exact(value, {"export_identifier"}, mode=mode)
        identifier = str(raw["export_identifier"])
        if identifier not in {"object_name", "label"}:
            raise NativeAnalyzeError("export_identifier must be object_name or label.")
        values = {"export_identifier": identifier}
    elif mode == "distance":
        names = {
            "distance_minimum_mm",
            "distance_maximum_mm",
            "size_minimum_mm",
            "size_maximum_mm",
            "linear_interpolation",
            "sampling",
        }
        raw = _exact(value, names, mode=mode)
        values = {
            name: _number(raw[name], field=name, exclusive=True)
            for name in names
            if name.endswith("_mm")
        }
        if values["distance_minimum_mm"] > values["distance_maximum_mm"]:
            raise NativeAnalyzeError("distance_minimum_mm cannot exceed distance_maximum_mm.")
        if values["size_minimum_mm"] > values["size_maximum_mm"]:
            raise NativeAnalyzeError("size_minimum_mm cannot exceed size_maximum_mm.")
        values["linear_interpolation"] = _bool(
            raw["linear_interpolation"], field="linear_interpolation"
        )
        values["sampling"] = _integer(raw["sampling"], field="sampling", maximum=1000)
    elif mode == "boundary_layer":
        raw = _exact(
            value,
            {"minimum_thickness_mm", "number_of_layers", "growth_rate"},
            mode=mode,
        )
        values = {
            "minimum_thickness_mm": _number(
                raw["minimum_thickness_mm"],
                field="minimum_thickness_mm",
                exclusive=True,
            ),
            "number_of_layers": _integer(
                raw["number_of_layers"], field="number_of_layers"
            ),
            "growth_rate": _number(
                raw["growth_rate"], field="growth_rate", exclusive=True
            ),
        }
    elif mode == "shape":
        raw = _exact(
            value,
            {"shape", "size_inside_mm", "size_outside_mm", "transition_thickness_mm"},
            mode=mode,
        )
        shape = raw["shape"]
        if not isinstance(shape, Mapping):
            raise NativeAnalyzeError("shape must be one typed box, sphere, or cylinder.")
        kind = str(shape.get("kind", ""))
        names = {
            "box": {"kind", "center_mm", "length_mm", "width_mm", "height_mm"},
            "sphere": {"kind", "center_mm", "radius_mm"},
            "cylinder": {"kind", "center_mm", "axis", "radius_mm"},
        }.get(kind)
        if names is None or set(shape) != names:
            raise NativeAnalyzeError("shape does not match one typed box, sphere, or cylinder.")
        prepared_shape = {
            "kind": kind,
            "center_mm": _vector(shape["center_mm"], field="shape.center_mm"),
        }
        for name in names - {"kind", "center_mm", "axis"}:
            prepared_shape[name] = _number(
                shape[name], field=f"shape.{name}", exclusive=True
            )
        if kind == "cylinder":
            prepared_shape["axis"] = _vector(
                shape["axis"], field="shape.axis", nonzero=True
            )
        values = {
            "shape": prepared_shape,
            "size_inside_mm": _number(
                raw["size_inside_mm"], field="size_inside_mm", exclusive=True
            ),
            "size_outside_mm": _number(
                raw["size_outside_mm"], field="size_outside_mm", exclusive=True
            ),
            "transition_thickness_mm": _number(
                raw["transition_thickness_mm"], field="transition_thickness_mm"
            ),
        }
        if kind == "cylinder" and values["transition_thickness_mm"] != 0.0:
            raise NativeAnalyzeError(
                "transition_thickness_mm must be zero for a cylinder, matching the human editor."
            )
    elif mode == "transfinite_curve":
        raw = _exact(
            value,
            {"nodes", "coefficient", "distribution", "inverted"},
            mode=mode,
        )
        distribution = str(raw["distribution"])
        if distribution not in {"constant", "bump", "progression"}:
            raise NativeAnalyzeError(
                "distribution must be constant, bump, or progression."
            )
        values = {
            "nodes": _integer(raw["nodes"], field="nodes", minimum=2, maximum=1000000),
            "coefficient": _number(
                raw["coefficient"], field="coefficient", exclusive=True
            ),
            "distribution": distribution,
            "inverted": _bool(raw["inverted"], field="inverted"),
        }
    else:
        fields = {
            "recombine",
            "triangle_orientation",
            "use_automation",
            "nodes",
            "coefficient",
            "distribution",
            "inverted",
        }
        if mode == "transfinite_volume":
            fields.add("mixed_elements")
        raw = _exact(value, fields, mode=mode)
        orientation = str(raw["triangle_orientation"])
        if orientation not in {"left", "right", "alternate_right", "alternate_left"}:
            raise NativeAnalyzeError("triangle_orientation is not supported.")
        distribution = str(raw["distribution"])
        if distribution not in {"constant", "bump", "progression"}:
            raise NativeAnalyzeError(
                "distribution must be constant, bump, or progression."
            )
        values = {
            "recombine": _bool(raw["recombine"], field="recombine"),
            "triangle_orientation": orientation,
            "use_automation": _bool(raw["use_automation"], field="use_automation"),
            "nodes": _integer(raw["nodes"], field="nodes", minimum=2, maximum=1000000),
            "coefficient": _number(
                raw["coefficient"], field="coefficient", exclusive=True
            ),
            "distribution": distribution,
            "inverted": _bool(raw["inverted"], field="inverted"),
        }
        if mode == "transfinite_volume":
            values["mixed_elements"] = _bool(
                raw["mixed_elements"], field="mixed_elements"
            )
    return PreparedRefinementValues(mode, values)


def apply_refinement_values(obj: Any, prepared: PreparedRefinementValues) -> None:
    values = prepared.values
    if prepared.mode == "region":
        obj.CharacteristicLength = f"{values['element_size_mm']} mm"
    elif prepared.mode == "group":
        obj.UseLabel = values["export_identifier"] == "label"
    elif prepared.mode == "distance":
        for field, native in (
            ("distance_minimum_mm", "DistanceMinimum"),
            ("distance_maximum_mm", "DistanceMaximum"),
            ("size_minimum_mm", "SizeMinimum"),
            ("size_maximum_mm", "SizeMaximum"),
        ):
            setattr(obj, native, f"{values[field]} mm")
        obj.LinearInterpolation = values["linear_interpolation"]
        obj.Sampling = values["sampling"]
    elif prepared.mode == "boundary_layer":
        obj.MinimumThickness = f"{values['minimum_thickness_mm']} mm"
        obj.NumberOfLayers = values["number_of_layers"]
        obj.GrowthRate = values["growth_rate"]
    elif prepared.mode == "shape":
        import FreeCAD as App

        shape = values["shape"]
        kind = shape["kind"]
        obj.ShapeType = kind.title()
        center = App.Vector(*(shape["center_mm"][axis] for axis in ("x", "y", "z")))
        if kind == "box":
            obj.BoxCenter = center
            obj.BoxLength = f"{shape['length_mm']} mm"
            obj.BoxWidth = f"{shape['width_mm']} mm"
            obj.BoxHeight = f"{shape['height_mm']} mm"
        elif kind == "sphere":
            obj.SphereCenter = center
            obj.SphereRadius = f"{shape['radius_mm']} mm"
        else:
            obj.CylinderCenter = center
            obj.CylinderAxis = App.Vector(*(shape["axis"][axis] for axis in ("x", "y", "z")))
            obj.CylinderRadius = f"{shape['radius_mm']} mm"
        obj.SizeIn = f"{values['size_inside_mm']} mm"
        obj.SizeOut = f"{values['size_outside_mm']} mm"
        obj.Thickness = f"{values['transition_thickness_mm']} mm"
    elif prepared.mode == "transfinite_curve":
        obj.Nodes = values["nodes"]
        obj.Coefficient = values["coefficient"]
        obj.Distribution = values["distribution"].title()
        obj.Invert = values["inverted"]
    else:
        obj.Recombine = values["recombine"]
        obj.TriangleOrientation = {
            "left": "Left",
            "right": "Right",
            "alternate_right": "AlternateRight",
            "alternate_left": "AlternateLeft",
        }[values["triangle_orientation"]]
        obj.UseAutomation = values["use_automation"]
        obj.Nodes = values["nodes"]
        obj.Coefficient = values["coefficient"]
        obj.Distribution = values["distribution"].title()
        obj.Invert = values["inverted"]
        if prepared.mode == "transfinite_volume":
            obj.MixedElements = values["mixed_elements"]
