# SPDX-License-Identifier: LGPL-2.1-or-later

"""Validation, creation, and exact proof for standalone Part primitives."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, object_reference


_FIELDS_BY_KIND = {
    "plane": ("length_mm", "width_mm"),
    "helix": (
        "pitch_mm",
        "height_mm",
        "radius_mm",
        "taper_degrees",
        "handedness",
    ),
    "spiral": ("growth_mm", "rotations", "radius_mm"),
    "circle": ("radius_mm", "start_degrees", "end_degrees"),
    "ellipse": (
        "major_radius_mm",
        "minor_radius_mm",
        "start_degrees",
        "end_degrees",
    ),
    "point": ("x_mm", "y_mm", "z_mm"),
    "line": (
        "start_x_mm",
        "start_y_mm",
        "start_z_mm",
        "end_x_mm",
        "end_y_mm",
        "end_z_mm",
    ),
    "regular_polygon": ("sides", "circumradius_mm"),
}
_TYPE_INFO = {
    "plane": ("Part::Plane", "Plane", "Face"),
    "helix": ("Part::Helix", "Helix", "Wire"),
    "spiral": ("Part::Spiral", "Spiral", "Wire"),
    "circle": ("Part::Circle", "Circle", "Edge"),
    "ellipse": ("Part::Ellipse", "Ellipse", "Edge"),
    "point": ("Part::Vertex", "Vertex", "Vertex"),
    "line": ("Part::Line", "Line", "Edge"),
    "regular_polygon": ("Part::RegularPolygon", "RegularPolygon", "Wire"),
}
_ANGLE_PROPERTIES = frozenset({"Angle", "Angle1", "Angle2"})
_TOLERANCE = 1.0e-8


@dataclass(frozen=True, slots=True)
class PartPrimitiveSpec:
    kind: str
    type_id: str
    base_name: str
    shape_type: str
    native_parameters: tuple[tuple[str, float | int | str], ...]

    @property
    def parameters(self) -> dict[str, float | int | str]:
        return dict(self.native_parameters)


def part_primitive_definition_fields() -> dict[str, frozenset[str]]:
    return {
        kind: frozenset(("kind", *fields))
        for kind, fields in _FIELDS_BY_KIND.items()
    }


def _finite_numbers(values: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, float]:
    numbers = {field: float(values[field]) for field in fields}
    if not all(math.isfinite(number) for number in numbers.values()):
        raise NativeModelError("Part primitive parameters must be finite.")
    return numbers


def _arc(values: Mapping[str, Any]) -> tuple[float, float]:
    angles = _finite_numbers(values, ("start_degrees", "end_degrees"))
    start = angles["start_degrees"]
    end = angles["end_degrees"]
    if start < 0.0 or end > 360.0 or start >= end:
        raise NativeModelError(
            "A Part curve requires increasing start and end angles from 0 to 360 degrees."
        )
    return start, end


def prepare_part_primitive(value: Mapping[str, Any]) -> PartPrimitiveSpec:
    if not isinstance(value, Mapping):
        raise NativeModelError("A Part primitive definition must be an object.")
    values = dict(value)
    kind = str(values.get("kind") or "").strip()
    expected = part_primitive_definition_fields().get(kind)
    if expected is None or set(values) != expected:
        raise NativeModelError("The Part primitive definition does not match its kind.")

    if kind == "plane":
        numbers = _finite_numbers(values, _FIELDS_BY_KIND[kind])
        if min(numbers.values()) <= 0.0:
            raise NativeModelError("A Part plane requires positive length and width.")
        native = {"Length": numbers["length_mm"], "Width": numbers["width_mm"]}
    elif kind == "helix":
        numbers = _finite_numbers(values, _FIELDS_BY_KIND[kind][:-1])
        pitch = numbers["pitch_mm"]
        height = numbers["height_mm"]
        radius = numbers["radius_mm"]
        taper = numbers["taper_degrees"]
        handedness = str(values["handedness"])
        if pitch <= 0.0 or height <= 0.0 or radius <= 0.0:
            raise NativeModelError("A Part helix requires positive pitch, height, and radius.")
        if not -89.9 <= taper <= 89.9:
            raise NativeModelError("A Part helix taper must be between -89.9 and 89.9 degrees.")
        if height / pitch > 10_000.0:
            raise NativeModelError("A Part helix cannot exceed 10,000 turns.")
        if radius + height * math.tan(math.radians(taper)) <= 0.0:
            raise NativeModelError("A Part helix taper must leave a positive end radius.")
        if handedness not in {"right", "left"}:
            raise NativeModelError("A Part helix handedness must be right or left.")
        native = {
            "Pitch": pitch,
            "Height": height,
            "Radius": radius,
            "Angle": taper,
            "LocalCoord": 0 if handedness == "right" else 1,
            "Style": 1,
        }
    elif kind == "spiral":
        numbers = _finite_numbers(values, _FIELDS_BY_KIND[kind])
        growth = numbers["growth_mm"]
        rotations = numbers["rotations"]
        radius = numbers["radius_mm"]
        if growth < 0.0 or rotations <= 0.0 or rotations > 1_000.0 or radius < 0.0:
            raise NativeModelError("A Part spiral has invalid growth, rotation, or radius values.")
        if radius <= 0.0 and growth <= 0.0:
            raise NativeModelError("A Part spiral requires a positive start radius or growth.")
        native = {"Growth": growth, "Rotations": rotations, "Radius": radius}
    elif kind == "circle":
        radius = float(values["radius_mm"])
        start, end = _arc(values)
        if not math.isfinite(radius) or radius <= 0.0:
            raise NativeModelError("A Part circle requires a positive finite radius.")
        native = {"Radius": radius, "Angle1": start, "Angle2": end}
    elif kind == "ellipse":
        radii = _finite_numbers(values, ("major_radius_mm", "minor_radius_mm"))
        major = radii["major_radius_mm"]
        minor = radii["minor_radius_mm"]
        start, end = _arc(values)
        if minor <= 0.0 or major < minor:
            raise NativeModelError(
                "A Part ellipse requires a positive minor radius no larger than its major radius."
            )
        native = {
            "MajorRadius": major,
            "MinorRadius": minor,
            "Angle1": start,
            "Angle2": end,
        }
    elif kind == "point":
        numbers = _finite_numbers(values, _FIELDS_BY_KIND[kind])
        native = {
            "X": numbers["x_mm"],
            "Y": numbers["y_mm"],
            "Z": numbers["z_mm"],
        }
    elif kind == "line":
        numbers = _finite_numbers(values, _FIELDS_BY_KIND[kind])
        start = tuple(numbers[f"start_{axis}_mm"] for axis in "xyz")
        end = tuple(numbers[f"end_{axis}_mm"] for axis in "xyz")
        if math.dist(start, end) <= _TOLERANCE:
            raise NativeModelError("A Part line requires two distinct endpoints.")
        native = {
            "X1": start[0],
            "Y1": start[1],
            "Z1": start[2],
            "X2": end[0],
            "Y2": end[1],
            "Z2": end[2],
        }
    else:
        sides = values["sides"]
        if type(sides) is not int or not 3 <= sides <= 1_000:
            raise NativeModelError("A regular Part polygon requires 3 to 1,000 sides.")
        radius = float(values["circumradius_mm"])
        if not math.isfinite(radius) or radius <= 0.0:
            raise NativeModelError("A regular Part polygon requires a positive radius.")
        native = {"Polygon": sides, "Circumradius": radius}

    type_id, base_name, shape_type = _TYPE_INFO[kind]
    return PartPrimitiveSpec(
        kind,
        type_id,
        base_name,
        shape_type,
        tuple(native.items()),
    )


def part_placement_from_mapping(value: Mapping[str, Any]) -> Any:
    import FreeCAD as App

    if not isinstance(value, Mapping) or set(value) != {"origin_mm", "rotation"}:
        raise NativeModelError("A Part primitive placement is invalid.")
    origin = value["origin_mm"]
    rotation = value["rotation"]
    if (
        not isinstance(origin, Mapping)
        or set(origin) != {"x", "y", "z"}
        or not isinstance(rotation, Mapping)
        or set(rotation) != {"axis", "angle_degrees"}
    ):
        raise NativeModelError("A Part primitive placement is invalid.")
    axis = rotation["axis"]
    if not isinstance(axis, Mapping) or set(axis) != {"x", "y", "z"}:
        raise NativeModelError("A Part primitive rotation axis is invalid.")
    origin_values = tuple(float(origin[name]) for name in "xyz")
    axis_values = tuple(float(axis[name]) for name in "xyz")
    angle = float(rotation["angle_degrees"])
    if not all(math.isfinite(number) for number in (*origin_values, *axis_values, angle)):
        raise NativeModelError("A Part primitive placement must contain finite numbers.")
    if math.sqrt(sum(component * component for component in axis_values)) < 1.0e-12:
        raise NativeModelError("A Part primitive rotation axis must be non-zero.")
    return App.Placement(
        App.Vector(*origin_values),
        App.Rotation(App.Vector(*axis_values), angle),
    )


def _property_number(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _set_native_parameter(obj: Any, name: str, value: float | int | str) -> None:
    if name in _ANGLE_PROPERTIES:
        setattr(obj, name, f"{float(value):.17g} deg")
    else:
        setattr(obj, name, value)


def create_part_primitive(
    document: Any,
    *,
    label: str,
    placement: Any,
    spec: PartPrimitiveSpec,
) -> NativeMutationDraft:
    import PartDesign

    obj = document.addObject(spec.type_id, spec.base_name)
    if obj is None or str(getattr(obj, "TypeId", "")) != spec.type_id:
        raise NativeModelError("The Part primitive factory returned the wrong object type.")
    obj.Label = label
    for name, value in spec.native_parameters:
        _set_native_parameter(obj, name, value)
    obj.Placement = placement
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    recomputed = document.recompute([obj], True, True)
    if recomputed is False or not obj.isValid():
        raise NativeModelError(
            str(obj.getStatusString() or "The Part primitive is invalid.")
        )
    PartDesign.finalizeDesignDefinition(obj)
    return NativeMutationDraft(
        value={
            "object": obj,
            "label": label,
            "placement": placement,
            "spec": spec,
        },
        recompute_targets=(obj,),
        created=(object_identity(obj),),
    )


def verify_part_primitive(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    obj = draft.value["object"]
    spec = draft.value["spec"]
    if (
        document.getObject(obj.Name) is not obj
        or obj.TypeId != spec.type_id
        or str(obj.Label) != draft.value["label"]
        or obj.Placement != draft.value["placement"]
        or not obj.isValid()
        or str(getattr(obj, "VibeCADTimelineRole", "") or "") != "operation"
        or not str(getattr(obj, "VibeCADDefinitionId", "") or "")
        or not str(getattr(obj, "DesignId", "") or "")
        or obj.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError("The Part primitive failed its exact object postcondition.")
    enum_expected = {
        "LocalCoord": {0: "Right-handed", 1: "Left-handed"},
        "Style": {1: "New style"},
    }
    for name, expected in spec.native_parameters:
        actual = getattr(obj, name)
        if name in enum_expected:
            if str(actual) != enum_expected[name][int(expected)]:
                raise NativeModelError("A Part primitive enum changed before commit.")
        elif abs(_property_number(actual) - float(expected)) > _TOLERANCE:
            raise NativeModelError("A Part primitive parameter changed before commit.")

    shape = obj.Shape
    if shape.isNull() or not shape.isValid() or shape.ShapeType != spec.shape_type:
        raise NativeModelError("The Part primitive did not create its expected valid shape.")
    vertex_count = len(shape.Vertexes)
    edge_count = len(shape.Edges)
    face_count = len(shape.Faces)
    if spec.kind == "plane":
        topology_ok = (vertex_count, edge_count, face_count) == (4, 4, 1)
    elif spec.kind in {"circle", "ellipse"}:
        span = float(spec.parameters["Angle2"]) - float(spec.parameters["Angle1"])
        expected_vertices = 1 if abs(span - 360.0) <= _TOLERANCE else 2
        topology_ok = (vertex_count, edge_count, face_count) == (
            expected_vertices,
            1,
            0,
        )
    elif spec.kind in {"helix", "spiral"}:
        topology_ok = face_count == 0 and edge_count >= 1 and vertex_count >= 2
    elif spec.kind == "point":
        topology_ok = (vertex_count, edge_count, face_count) == (1, 0, 0)
    elif spec.kind == "line":
        topology_ok = (vertex_count, edge_count, face_count) == (2, 1, 0)
    else:
        sides = int(spec.parameters["Polygon"])
        topology_ok = (vertex_count, edge_count, face_count) == (sides, sides, 0)
    if not topology_ok:
        raise NativeModelError("The Part primitive topology changed before commit.")

    result: dict[str, Any] = {
        "object": object_reference(obj),
        "primitive_kind": spec.kind,
        "shape_type": shape.ShapeType,
        "vertex_count": vertex_count,
        "edge_count": edge_count,
        "face_count": face_count,
    }
    if face_count:
        result["area_mm2"] = float(shape.Area)
    elif edge_count:
        result["length_mm"] = float(shape.Length)
    return result
