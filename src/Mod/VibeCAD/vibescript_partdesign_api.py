# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Part Design VibeScript programs.

The provider authors a declarative Body/sketch/feature graph.  The graph is
evaluated only in an isolated ``FreeCADCmd`` document; source never receives a
live document object or a GUI binding.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from functools import wraps
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_component_api import component_value, instance_values
from vibescript_material_api import MaterialDomainAPI
from vibescript_part_api import PartDomainAPI
from vibescript_sketcher_api import SketcherDomainAPI


_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_PLANES = frozenset({"XY", "XZ", "YZ"})
_AXES = frozenset({"H", "V", "N", "X", "Y", "Z"})
_QUERY_FIELDS = frozenset(
    {
        "type",
        "element_type",
        "expected_count",
        "geometry_type",
        "normal",
        "normal_tolerance_degrees",
        "direction",
        "direction_tolerance_degrees",
        "radius",
        "radius_tolerance",
        "min_area",
        "max_area",
        "min_length",
        "max_length",
        "near_point",
        "max_distance",
    }
)
_PUBLIC_QUERY_FIELD_ALIASES = {
    "radius_mm": "radius",
    "radius_tolerance_mm": "radius_tolerance",
    "min_area_mm2": "min_area",
    "max_area_mm2": "max_area",
    "min_length_mm": "min_length",
    "max_length_mm": "max_length",
    "max_distance_mm": "max_distance",
}
_SKETCH_EXPORTS = SketcherDomainAPI.exported_names
_PUBLISHABLE_TYPES = ("solid", "shell", "face", "wire", "compound")
_PUBLIC_OUTPUT_TYPES = (*_PUBLISHABLE_TYPES, "component_link")
_TOPOLOGY_TYPES = frozenset({"edge", *_PUBLISHABLE_TYPES})
_MATERIAL_OPERATIONS = frozenset({"add_material", "remove_material"})
_CREATION_OPERATIONS = frozenset({"new_solid", "new_surface"})
_LINEAR_DIRECTIONS = frozenset(
    {"along_normal", "opposite_normal", "symmetric"}
)
_COMPATIBILITY_METHODS = frozenset({"pad", "pocket", "groove"})
_COMPATIBILITY_FEATURES = frozenset(
    {*_COMPATIBILITY_METHODS, "loft_subtractive"}
)
_CONNECTOR_KINDS = frozenset({"axis", "plane", "point", "frame"})
_CONNECTOR_COMPATIBILITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_BASIC_MEASURE_QUANTITIES = frozenset(
    {
        "length_mm",
        "area_mm2",
        "volume_mm3",
        "solid_count",
        "face_count",
        "edge_count",
        "bounds_min_x_mm",
        "bounds_min_y_mm",
        "bounds_min_z_mm",
        "bounds_max_x_mm",
        "bounds_max_y_mm",
        "bounds_max_z_mm",
        "bounds_size_x_mm",
        "bounds_size_y_mm",
        "bounds_size_z_mm",
        "center_of_mass_x_mm",
        "center_of_mass_y_mm",
        "center_of_mass_z_mm",
    }
)
_PAIR_MEASURE_QUANTITIES = frozenset(
    {"minimum_distance_mm", "interference_volume_mm3"}
)
_RADIAL_MEASURE_QUANTITIES = frozenset({"radius_mm", "diameter_mm"})
_MASS_MEASURE_QUANTITIES = frozenset(
    {
        "mass_kg",
        "inertia_xx_kg_mm2",
        "inertia_xy_kg_mm2",
        "inertia_xz_kg_mm2",
        "inertia_yy_kg_mm2",
        "inertia_yz_kg_mm2",
        "inertia_zz_kg_mm2",
    }
)
_MEASURE_QUANTITIES = frozenset(
    {
        *_BASIC_MEASURE_QUANTITIES,
        *_PAIR_MEASURE_QUANTITIES,
        *_RADIAL_MEASURE_QUANTITIES,
        *_MASS_MEASURE_QUANTITIES,
        "minimum_wall_thickness_mm",
    }
)
_PART_API_EXPORTS = PartDomainAPI.exported_names.fget(None)
_MATERIAL_API_EXPORTS = MaterialDomainAPI.exported_names

# Part's direct OCC graph is retained as an implementation library after the
# standalone workbench is retired.  Non-conflicting operations keep their
# concise name.  Curve constructors that would otherwise collide with 2D
# Sketcher geometry say ``*_3d`` explicitly; the canonical modeling methods
# below own extrude/revolve/loft/sweep/mirror/fillet/chamfer/thickness.
_DIRECT_PART_EXPORTS: tuple[tuple[str, str], ...] = (
    ("from_object", "from_object"),
    ("box", "box"),
    ("wedge", "wedge"),
    ("plane", "plane"),
    ("prism", "prism"),
    ("cylinder", "cylinder"),
    ("cone", "cone"),
    ("sphere", "sphere"),
    ("torus", "torus"),
    ("line_3d", "line"),
    ("arc_3d", "arc"),
    ("circle_3d", "circle"),
    ("ellipse_3d", "ellipse"),
    ("bezier_3d", "bezier"),
    ("bspline_3d", "bspline"),
    ("nurbs_curve", "nurbs_curve"),
    ("helix_curve", "helix"),
    ("wire", "wire"),
    ("face", "face"),
    ("shell", "shell"),
    ("solid", "solid"),
    ("compound", "compound"),
    ("subshape", "subshape"),
    ("ruled_surface", "ruled_surface"),
    ("filled_surface", "filled_surface"),
    ("section", "section"),
    ("general_fuse", "general_fuse"),
    ("slice", "slice"),
    ("defeature", "defeature"),
    ("to_nurbs", "to_nurbs"),
    ("reverse", "reverse"),
    ("sew", "sew"),
    ("repair", "repair"),
    ("offset", "offset"),
    ("offset2d", "offset2d"),
    ("transform", "transform"),
    ("project", "project"),
    ("refine", "refine"),
)


def _error(operation: str, parameter: str, reason: str, value: Any = None) -> ValueError:
    suffix = "" if value is None else f"; received {value!r}"
    return ValueError(f"api.{operation}: {parameter} {reason}{suffix}.")


def _linear_feature_direction(
    operation: str,
    direction: str | None,
    *,
    reverse: bool,
    midplane: bool,
    subtractive: bool,
) -> tuple[bool, bool, str]:
    """Map one public direction to FreeCAD's operation-specific booleans.

    FreeCAD's native Reversed flag points an additive Pad opposite its sketch
    normal, but points a subtractive Pocket/Hole along its sketch normal.  The
    public API intentionally hides that inconsistency behind one vocabulary.
    """

    if direction is None:
        semantic = (
            "symmetric"
            if bool(midplane)
            else "along_normal"
            if bool(reverse) == bool(subtractive)
            else "opposite_normal"
        )
        return bool(reverse), bool(midplane), semantic
    clean = str(direction or "").strip().lower()
    if clean not in _LINEAR_DIRECTIONS:
        raise _error(
            operation,
            "direction",
            "must be along_normal, opposite_normal, or symmetric",
            direction,
        )
    if reverse or midplane:
        raise _error(
            operation,
            "direction",
            "cannot be combined with reverse or midplane",
        )
    if clean == "symmetric":
        return False, True, clean
    native_reverse = (
        clean == "along_normal"
        if subtractive
        else clean == "opposite_normal"
    )
    return native_reverse, False, clean


def _number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float | None = None,
    strict: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(operation, parameter, "must be a finite number", value)
    result = float(value)
    if not math.isfinite(result):
        raise _error(operation, parameter, "must be finite", value)
    if minimum is not None and (result <= minimum if strict else result < minimum):
        relation = "greater than" if strict else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    return result


def _integer(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: int,
    maximum: int = 10_000,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(operation, parameter, "must be an integer", value)
    if not minimum <= value <= maximum:
        raise _error(
            operation,
            parameter,
            f"must be between {minimum} and {maximum}",
            value,
        )
    return int(value)


def _label(operation: str, value: Any) -> str:
    result = str(value or "").strip()
    if len(result) > 256:
        raise _error(operation, "label", "must contain at most 256 characters")
    return result


def _required_text(
    operation: str,
    parameter: str,
    value: Any,
    *,
    maximum: int = 128,
) -> str:
    result = str(value or "").strip()
    if not result:
        raise _error(operation, parameter, "must be non-empty")
    if len(result) > maximum:
        raise _error(
            operation,
            parameter,
            f"must contain at most {maximum} characters",
        )
    return result


def _fastener_options(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _error("fastener", "options", "must be an object", value)
    if len(value) > 16:
        raise _error("fastener", "options", "may contain at most 16 entries")
    result: dict[str, Any] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise _error(
                "fastener",
                "options",
                "keys must use lower_snake_case",
                raw_name,
            )
        if isinstance(raw_value, float) and not math.isfinite(raw_value):
            raise _error(
                "fastener",
                f"options.{name}",
                "must be finite",
                raw_value,
            )
        if not isinstance(raw_value, (str, bool, int, float)):
            raise _error(
                "fastener",
                f"options.{name}",
                "must be a string, boolean, integer, or finite number",
                raw_value,
            )
        result[name] = raw_value
    return result


def _retag(value: Any, domain: str) -> Any:
    """Retag the shared Sketcher value graph without exposing another API."""

    if isinstance(value, DomainValue):
        return DomainValue(
            domain=domain,
            operation=value.operation,
            output_type=value.output_type,
            arguments=tuple(_retag(item, domain) for item in value.arguments),
            properties={key: _retag(item, domain) for key, item in value.properties.items()},
        )
    if isinstance(value, Mapping):
        return {str(key): _retag(item, domain) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_retag(item, domain) for item in value]
    return value


def _value(value: Any, output_types: set[str], parameter: str, operation: str) -> DomainValue:
    if (
        not isinstance(value, DomainValue)
        or value.domain != "partdesign"
        or value.output_type not in output_types
    ):
        raise _error(
            operation,
            parameter,
            f"must be a value returned by this Part Design api with type {sorted(output_types)}",
            type(value).__name__,
        )
    return value


def _profile(operation: str, parameter: str, value: Any) -> DomainValue:
    return _value(value, {"profile"}, parameter, operation)


def _hole_location_profile(operation: str, value: Any) -> DomainValue:
    """Make point centers defining geometry for FreeCAD's native Hole feature."""

    profile = _profile(operation, "profile", value)
    if not profile.arguments:
        return profile
    geometry = profile.arguments[0]
    if not isinstance(geometry, tuple):
        return profile
    normalized = []
    changed = False
    for item in geometry:
        if (
            isinstance(item, DomainValue)
            and item.operation == "point"
            and bool(item.properties.get("construction"))
        ):
            properties = dict(item.properties)
            properties["construction"] = False
            item = DomainValue(
                domain=item.domain,
                operation=item.operation,
                output_type=item.output_type,
                arguments=item.arguments,
                properties=properties,
            )
            changed = True
        normalized.append(item)
    if not changed:
        return profile
    return DomainValue(
        domain=profile.domain,
        operation=profile.operation,
        output_type=profile.output_type,
        arguments=(tuple(normalized), *profile.arguments[1:]),
        properties=profile.properties,
    )


def _feature(operation: str, parameter: str, value: Any) -> DomainValue:
    return _value(value, {"feature"}, parameter, operation)


def _topology(
    operation: str,
    parameter: str,
    value: Any,
    *,
    allowed: Iterable[str] = _TOPOLOGY_TYPES,
) -> DomainValue:
    return _value(value, set(allowed), parameter, operation)


def _modeled(
    operation: str,
    parameter: str,
    value: Any,
    *,
    topology: Iterable[str] = _TOPOLOGY_TYPES,
) -> DomainValue:
    return _value(value, {"feature", *set(topology)}, parameter, operation)


def _material_card(
    operation: str,
    parameter: str,
    value: Any,
    *,
    optional: bool = False,
) -> DomainValue | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, DomainValue)
        or value.domain != "partdesign"
        or value.operation != "material"
        or value.output_type != "material_card"
    ):
        raise _error(
            operation,
            parameter,
            "must be the exact value returned by api.material",
            type(value).__name__,
        )
    return value


def _appearance(
    operation: str,
    parameter: str,
    value: Any,
    *,
    optional: bool = False,
) -> DomainValue | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, DomainValue)
        or value.domain != "partdesign"
        or value.operation != "appearance"
        or value.output_type != "appearance"
    ):
        raise _error(
            operation,
            parameter,
            "must be the exact value returned by api.appearance",
            type(value).__name__,
        )
    return value


def _operation_intent(
    operation: str,
    value: Any,
    *,
    allow_creation: bool,
) -> str:
    result = str(value or "").strip().lower()
    allowed = set(_MATERIAL_OPERATIONS)
    if allow_creation:
        allowed.update(_CREATION_OPERATIONS)
    if result not in allowed:
        raise _error(
            operation,
            "operation",
            f"must be one of {sorted(allowed)}",
            value,
        )
    return result


def _publishable_type(operation: str, value: Any) -> str:
    result = str(value or "").strip().lower()
    if result not in _PUBLISHABLE_TYPES:
        raise _error(
            operation,
            "output_type",
            f"must be one of {list(_PUBLISHABLE_TYPES)}",
            value,
        )
    return result


def _plane(operation: str, value: Any) -> str:
    result = str(value or "").strip().upper()
    if result not in _PLANES:
        raise _error(operation, "plane", f"must be one of {sorted(_PLANES)}", value)
    return result


def _axis(operation: str, value: Any) -> str:
    result = str(value or "").strip().upper()
    if result not in _AXES:
        raise _error(operation, "axis", f"must be one of {sorted(_AXES)}", value)
    return result


def _global_axis(operation: str, value: Any) -> str:
    result = str(value or "").strip().upper()
    if result not in {"X", "Y", "Z"}:
        raise _error(operation, "axis", "must be X, Y, or Z", value)
    return result


def _vector(operation: str, parameter: str, value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "must be [x, y, z]", value)
    return [_number(operation, f"{parameter}[{index}]", item) for index, item in enumerate(value)]


def _centers_mm(
    operation: str,
    value: Any,
    *,
    maximum: int = 256,
) -> list[list[float]]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= maximum:
        raise _error(
            operation,
            "centers_mm",
            f"must contain 1-{maximum} [u, v] coordinates",
            value,
        )
    if len(value) == 2 and all(
        not isinstance(component, (list, tuple)) for component in value
    ):
        value = [value]
    result: list[list[float]] = []
    for index, center in enumerate(value):
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            raise _error(
                operation,
                f"centers_mm[{index}]",
                "must be [u, v]",
                center,
            )
        result.append(
            [
                _number(operation, f"centers_mm[{index}][0]", center[0]),
                _number(operation, f"centers_mm[{index}][1]", center[1]),
            ]
        )
    return result


def _rgb255(
    operation: str,
    parameter: str,
    value: Any,
) -> list[float] | None:
    """Normalize explicit 8-bit RGB to FreeCAD's native 0-1 channels."""

    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(
            operation,
            parameter,
            "must be three integer RGB channels in the inclusive range 0-255",
            value,
        )
    result: list[float] = []
    for index, channel in enumerate(value):
        if (
            isinstance(channel, bool)
            or type(channel) is not int
            or not 0 <= channel <= 255
        ):
            raise _error(
                operation,
                f"{parameter}[{index}]",
                "must be an integer in the inclusive range 0-255",
                channel,
            )
        result.append(float(channel) / 255.0)
    return result


def _nonzero_vector(operation: str, parameter: str, value: Any) -> list[float]:
    result = _vector(operation, parameter, value)
    if math.sqrt(sum(component * component for component in result)) <= 1.0e-12:
        raise _error(operation, parameter, "must be non-zero", value)
    return result


def _oriented_frame(
    operation: str,
    parameter: str,
    value: Any,
    *,
    axis_name: str,
) -> dict[str, list[float]]:
    required = {"origin", axis_name, "x_direction"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise _error(
            operation,
            parameter,
            f"must contain exactly origin, {axis_name}, and x_direction",
            value,
        )
    origin = _vector(operation, f"{parameter}.origin", value["origin"])
    axis = _nonzero_vector(operation, f"{parameter}.{axis_name}", value[axis_name])
    x_direction = _nonzero_vector(
        operation,
        f"{parameter}.x_direction",
        value["x_direction"],
    )
    axis_length = math.sqrt(sum(component * component for component in axis))
    unit_axis = [component / axis_length for component in axis]
    dot = sum(
        component * axis_component
        for component, axis_component in zip(x_direction, unit_axis)
    )
    projected_x = [
        component - dot * axis_component
        for component, axis_component in zip(x_direction, unit_axis)
    ]
    projected_length = math.sqrt(sum(component * component for component in projected_x))
    if projected_length <= 1.0e-12:
        raise _error(
            operation,
            f"{parameter}.x_direction",
            f"must not be parallel to {parameter}.{axis_name}",
            value["x_direction"],
        )
    return {
        "origin": origin,
        axis_name: unit_axis,
        "x_direction": [component / projected_length for component in projected_x],
    }


def _sketch_placement(value: Any) -> dict[str, list[float]] | None:
    if value is None:
        return None
    return _oriented_frame(
        "sketch",
        "placement",
        value,
        axis_name="normal",
    )


def _selection(
    operation: str,
    value: Any,
    *,
    element_type: str | None = None,
    allow_all_edges: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        if isinstance(value, str) and re.fullmatch(
            r"(?:Face|Edge|Vertex)[1-9][0-9]*", value
        ):
            raise _error(
                operation,
                "selection",
                "must be a geometric query; transient FaceN/EdgeN names are forbidden",
                value,
            )
        raise _error(operation, "selection", "must be an object", value)
    clean = {str(key): item for key, item in value.items()}
    mode = str(clean.get("type") or "")
    if mode == "all_edges":
        if not allow_all_edges or set(clean) != {"type"}:
            raise _error(operation, "selection", "all_edges is not valid here", value)
        return {"type": "all_edges"}
    if mode != "query":
        raise _error(
            operation,
            "selection",
            "must be a geometric query; transient FaceN/EdgeN names are forbidden",
            value,
        )
    for public_name, stored_name in _PUBLIC_QUERY_FIELD_ALIASES.items():
        if public_name not in clean:
            continue
        if stored_name in clean:
            raise _error(
                operation,
                "selection",
                f"must not provide both {public_name} and {stored_name}",
                value,
            )
        clean[stored_name] = clean.pop(public_name)
    if "angle_tolerance_degrees" in clean:
        tolerance = clean.pop("angle_tolerance_degrees")
        targets = [
            name
            for vector, name in (
                ("normal", "normal_tolerance_degrees"),
                ("direction", "direction_tolerance_degrees"),
            )
            if vector in clean
        ]
        if not targets:
            raise _error(
                operation,
                "selection.angle_tolerance_degrees",
                "requires normal or direction",
            )
        for target in targets:
            if target in clean:
                raise _error(
                    operation,
                    "selection",
                    f"must not provide both angle_tolerance_degrees and {target}",
                    value,
                )
            clean[target] = tolerance
    unsupported = sorted(set(clean) - _QUERY_FIELDS)
    if unsupported:
        raise _error(
            operation,
            "selection",
            "has unsupported fields "
            f"{unsupported}; use the arguments documented by api.find_subelements",
            value,
        )
    kind = str(clean.get("element_type") or "")
    if kind not in {"face", "edge"} or (element_type and kind != element_type):
        raise _error(operation, "selection.element_type", "has the wrong topology type", kind)
    count = _integer(
        operation,
        "selection.expected_count",
        clean.get("expected_count"),
        minimum=1,
        maximum=256,
    )
    result: dict[str, Any] = {
        "type": "query",
        "element_type": kind,
        "expected_count": count,
    }
    for key in (
        "geometry_type",
        "normal_tolerance_degrees",
        "direction_tolerance_degrees",
        "radius",
        "radius_tolerance",
        "min_area",
        "max_area",
        "min_length",
        "max_length",
        "max_distance",
    ):
        if key not in clean:
            continue
        if key == "geometry_type":
            text = str(clean[key] or "").strip()
            if not text:
                raise _error(operation, f"selection.{key}", "must be non-empty")
            result[key] = text
        else:
            result[key] = _number(
                operation,
                f"selection.{key}",
                clean[key],
                minimum=0.0,
            )
    for key in ("normal", "direction", "near_point"):
        if key in clean:
            result[key] = (
                _vector(operation, f"selection.{key}", clean[key])
                if key == "near_point"
                else _nonzero_vector(operation, f"selection.{key}", clean[key])
            )
    return result


def _interfaces(value: Any) -> dict[str, dict[str, Any]]:
    """Validate semantic names in the local namespace of one published output."""

    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping) or len(value) > 64:
        raise _error("body", "interfaces", "must map at most 64 names to contracts", value)
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw in value.items():
        name = str(raw_name or "").strip()
        if not _NAME.fullmatch(name):
            raise _error("body", f"interfaces[{raw_name!r}]", "has an invalid stable name")
        if not isinstance(raw, Mapping) or not set(raw) <= {
            "selection",
            "description",
            "connector",
        }:
            raise _error(
                "body",
                f"interfaces[{name}]",
                "must contain selection with optional description and connector contract",
                raw,
            )
        selection = raw.get("selection")
        if isinstance(selection, Mapping) and selection.get("type") == "origin":
            if set(selection) != {"type"}:
                raise _error("body", f"interfaces[{name}].selection", "origin accepts only type")
            clean_selection = {"type": "origin"}
        elif isinstance(selection, Mapping) and selection.get("type") == "frame":
            frame = _oriented_frame(
                "body",
                f"interfaces[{name}].selection",
                {key: value for key, value in selection.items() if key != "type"},
                axis_name="axis_direction",
            )
            clean_selection = {"type": "frame", **frame}
        else:
            clean_selection = _selection("body", selection)
        description = str(raw.get("description") or "").strip()
        if len(description) > 500:
            raise _error("body", f"interfaces[{name}].description", "is too long")
        connector = raw.get("connector")
        clean_connector: dict[str, Any] | None = None
        if connector is not None:
            if not isinstance(connector, Mapping) or not set(connector) <= {
                "kind",
                "allowed_joints",
                "compatibility",
            }:
                raise _error(
                    "body",
                    f"interfaces[{name}].connector",
                    "must contain kind with optional allowed_joints and compatibility",
                    connector,
                )
            kind = str(connector.get("kind") or "").strip().lower()
            if kind not in _CONNECTOR_KINDS:
                raise _error(
                    "body",
                    f"interfaces[{name}].connector.kind",
                    f"must be one of {sorted(_CONNECTOR_KINDS)}",
                    kind,
                )
            clean_connector = {"kind": kind}
            if "allowed_joints" in connector:
                from vibescript_assembly_api import JOINT_TYPES

                raw_joints = connector.get("allowed_joints")
                if (
                    not isinstance(raw_joints, (list, tuple))
                    or not raw_joints
                    or len(raw_joints) > len(JOINT_TYPES)
                ):
                    raise _error(
                        "body",
                        f"interfaces[{name}].connector.allowed_joints",
                        "must be a non-empty list of Assembly joint kinds",
                        raw_joints,
                    )
                joints = [str(value or "").strip().lower() for value in raw_joints]
                if len(joints) != len(set(joints)) or any(
                    value not in JOINT_TYPES for value in joints
                ):
                    raise _error(
                        "body",
                        f"interfaces[{name}].connector.allowed_joints",
                        f"must contain unique values from {list(JOINT_TYPES)}",
                        raw_joints,
                    )
                clean_connector["allowed_joints"] = joints
            if "compatibility" in connector:
                compatibility = str(connector.get("compatibility") or "").strip()
                if not _CONNECTOR_COMPATIBILITY.fullmatch(compatibility):
                    raise _error(
                        "body",
                        f"interfaces[{name}].connector.compatibility",
                        "must be a stable token using letters, digits, '.', '_', ':', or '-'",
                        compatibility,
                    )
                clean_connector["compatibility"] = compatibility
        result[name] = {
            "selection": clean_selection,
            **({"description": description} if description else {}),
            **({"connector": clean_connector} if clean_connector is not None else {}),
        }
    return result


class PartDesignDomainAPI:
    """Unified parametric modeling graph API injected into Part Design source."""

    __slots__ = (
        "_material",
        "_next_feature_id",
        "_part",
        "_sketch_values",
        "_sketcher",
    )

    domain = "partdesign"
    exported_names = (
        # Stable document references and standalone primitives.
        "from_object",
        "box",
        "wedge",
        "plane",
        "prism",
        "cylinder",
        "cone",
        "sphere",
        "torus",
        "fastener",
        "component",
        "instances",
        # Sketch geometry.  Explicit *_3d names below avoid dimensional ambiguity.
        "point",
        "line",
        "arc",
        "circle",
        "ellipse",
        "bspline",
        "external_geometry",
        "constraint",
        "sketch",
        "line_3d",
        "arc_3d",
        "circle_3d",
        "ellipse_3d",
        "bezier_3d",
        "bspline_3d",
        "nurbs_curve",
        "helix_curve",
        "wire",
        "face",
        "shell",
        "solid",
        "compound",
        "subshape",
        # One canonical operation per modeling intent.
        "extrude",
        "revolve",
        "loft",
        "sweep",
        "helix",
        "boolean",
        "union",
        "cut",
        "intersect",
        "section",
        "general_fuse",
        "slice",
        "ruled_surface",
        "filled_surface",
        "polar_pattern",
        "linear_pattern",
        "multi_transform",
        "mirror",
        "fillet",
        "chamfer",
        "thickness",
        "move_planar_faces",
        "hole",
        "holes",
        "bosses",
        "fastener_hole",
        "involute_gear",
        "draft",
        # Distinct topology, repair, and transformation capabilities.
        "defeature",
        "to_nurbs",
        "reverse",
        "sew",
        "repair",
        "offset",
        "offset2d",
        "transform",
        "project",
        "refine",
        # Declarative inspection and publication.
        "find_subelements",
        "measure",
        "minimum_distance",
        "material",
        "appearance",
        "body",
        "publish",
    )

    def __init__(
        self,
        exports: Iterable[str],
        output_types: Iterable[str],
    ) -> None:
        declared = tuple(dict.fromkeys(str(item) for item in exports))
        if declared != self.exported_names:
            raise RuntimeError(
                "Part Design pack exports do not match the runtime contract: "
                f"expected {self.exported_names!r}, received {declared!r}."
            )
        declared_output_types = tuple(
            dict.fromkeys(str(item) for item in output_types)
        )
        if declared_output_types not in {_PUBLISHABLE_TYPES, _PUBLIC_OUTPUT_TYPES}:
            raise RuntimeError(
                "Part Design publication types do not match the unified modeling contract."
            )
        object.__setattr__(
            self,
            "_sketcher",
            SketcherDomainAPI(_SKETCH_EXPORTS, ("sketch",)),
        )
        object.__setattr__(
            self,
            "_part",
            PartDomainAPI(_PART_API_EXPORTS, _PUBLISHABLE_TYPES),
        )
        object.__setattr__(
            self,
            "_material",
            MaterialDomainAPI(
                _MATERIAL_API_EXPORTS,
                ("material_assignment", "appearance"),
            ),
        )
        object.__setattr__(self, "_sketch_values", {})
        object.__setattr__(self, "_next_feature_id", 1)

    def component(
        self,
        source: Mapping[str, str],
        *,
        placement: Sequence[float] | Mapping[str, Any] | None = None,
        interfaces: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Place one linked occurrence from an ``available_components`` reference.

        Editing the source updates the occurrence. Placement is ``[x,y,z]`` or
        position plus quaternion/axis-angle. Use this—not
        ``from_object``/``transform``/``publish``—for reusable parts. Use
        ``interfaces`` to publish explicit origin/frame connectors on an imported
        component without copying its BREP.
        """

        return component_value(
            self.domain,
            source,
            placement=placement,
            interfaces=interfaces or {},
            label=label,
        )

    def instances(
        self,
        source: Mapping[str, str],
        placements: Sequence[
            Sequence[float] | Mapping[str, Any] | None
        ],
        *,
        labels: Sequence[str] | None = None,
        interfaces: Mapping[str, Any] | None = None,
    ) -> tuple[DomainValue, ...]:
        """Place repeated lightweight occurrences of one reusable component.

        Placements use ``api.component`` forms. Source BREP is linked, not rebuilt.
        """

        return instance_values(
            self.domain,
            source,
            placements,
            labels=labels,
            interfaces=interfaces or {},
        )

    def _from_sketcher(self, value: DomainValue) -> DomainValue:
        wrapped = _retag(value, "partdesign")
        self._sketch_values[id(wrapped)] = value
        return wrapped

    def _to_sketcher(self, value: Any, *, operation: str, parameter: str) -> Any:
        if isinstance(value, DomainValue):
            original = self._sketch_values.get(id(value))
            if original is None:
                raise _error(
                    operation,
                    parameter,
                    "must reuse the exact geometry or constraint value returned by this api",
                )
            return original
        if isinstance(value, Mapping):
            return {
                str(key): self._to_sketcher(
                    item,
                    operation=operation,
                    parameter=f"{parameter}.{key}",
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                self._to_sketcher(
                    item,
                    operation=operation,
                    parameter=f"{parameter}[{index}]",
                )
                for index, item in enumerate(value)
            ]
        return value

    def _feature_id(self) -> str:
        value = int(self._next_feature_id)
        object.__setattr__(self, "_next_feature_id", value + 1)
        return f"f{value}"

    def _graph(
        self,
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain="partdesign",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties={"graph_id": self._feature_id(), **properties},
        )

    def _feature_base(
        self,
        operation: str,
        base: DomainValue,
        *,
        label: str,
    ) -> DomainValue:
        clean_base = _modeled(operation, "base", base, topology={"solid"})
        if clean_base.output_type == "feature":
            return clean_base
        return self._graph(
            "base_feature",
            "feature",
            clean_base,
            label=f"{label} base" if label else "Base feature",
        )

    def _direct(self, part_operation: str, *arguments: Any, **properties: Any) -> DomainValue:
        """Call the retained OCC graph library and retag it for this domain."""

        method = getattr(self._part, part_operation)
        value = method(
            *tuple(_retag(item, "part") for item in arguments),
            **{key: _retag(item, "part") for key, item in properties.items()},
        )
        return _retag(value, "partdesign")

    def transform(
        self,
        shape: DomainValue,
        *,
        translation: Sequence[float] = (0.0, 0.0, 0.0),
        rotation_axis: Sequence[float] = (0.0, 0.0, 1.0),
        rotation_degrees: float = 0.0,
        scale: float | Sequence[float] = 1.0,
        pivot: Sequence[float] = (0.0, 0.0, 0.0),
        label: str = "",
    ) -> DomainValue:
        """Copy and place topology or a Body feature; scale, then rotate about pivot, then translate. A feature becomes solid topology without losing its source graph."""

        operation = "transform"
        clean_shape = _modeled(operation, "shape", shape)
        if isinstance(scale, (list, tuple)):
            clean_scale = _vector(operation, "scale", scale)
            if any(value <= 0.0 for value in clean_scale):
                raise _error(
                    operation,
                    "scale",
                    "all scale factors must be positive",
                    scale,
                )
        else:
            factor = _number(operation, "scale", scale, minimum=0.0, strict=True)
            clean_scale = [factor, factor, factor]
        return self._graph(
            operation,
            "solid" if clean_shape.output_type == "feature" else clean_shape.output_type,
            clean_shape,
            translation=_vector(operation, "translation", translation),
            rotation_axis=_nonzero_vector(operation, "rotation_axis", rotation_axis),
            rotation_degrees=_number(
                operation,
                "rotation_degrees",
                rotation_degrees,
            ),
            scale=clean_scale,
            pivot=_vector(operation, "pivot", pivot),
            label=_label(operation, label),
        )

    def fastener(
        self,
        standard: str,
        nominal_thread: str,
        *,
        length_mm: float | None = None,
        model_thread: bool = True,
        left_handed: bool = False,
        options: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create one exact catalog fastener as a native parametric Body feature.

        Use the published standard, size/thread token, and a catalog length.
        Real helical thread geometry is the default; set model_thread=False only
        for a deliberate lightweight envelope. No nearest-size substitution is
        performed.
        """

        if not isinstance(model_thread, bool):
            raise _error(
                "fastener",
                "model_thread",
                "must be a boolean",
                model_thread,
            )
        if not isinstance(left_handed, bool):
            raise _error(
                "fastener",
                "left_handed",
                "must be a boolean",
                left_handed,
            )
        return self._graph(
            "fastener",
            "feature",
            _required_text("fastener", "standard", standard),
            _required_text(
                "fastener",
                "nominal_thread",
                nominal_thread,
            ),
            length_mm=(
                None
                if length_mm is None
                else _number(
                    "fastener",
                    "length_mm",
                    length_mm,
                    minimum=0.0,
                    strict=True,
                )
            ),
            model_thread=model_thread,
            left_handed=left_handed,
            options=_fastener_options(options),
            label=_label("fastener", label),
        )

    def involute_gear(
        self,
        teeth: int,
        module_mm: float,
        width_mm: float,
        *,
        pressure_angle_degrees: float = 20.0,
        bore_diameter_mm: float = 0.0,
        internal: bool = False,
        outer_diameter_mm: float | None = None,
        high_precision: bool = True,
        addendum_coefficient: float | None = None,
        dedendum_coefficient: float = 1.25,
        root_fillet_coefficient: float = 0.38,
        profile_shift_coefficient: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Create an exact involute spur-gear feature.

        Pitch diameter is teeth * module_mm. External gears accept a bore; internal
        gears require outer_diameter_mm. Publish a named axis with api.body, then
        use api.joint('gears') with pitch radii in Assembly. Never approximate teeth.
        """

        operation = "involute_gear"
        if not isinstance(internal, bool):
            raise _error(operation, "internal", "must be a boolean", internal)
        if not isinstance(high_precision, bool):
            raise _error(
                operation,
                "high_precision",
                "must be a boolean",
                high_precision,
            )
        clean_bore = _number(
            operation,
            "bore_diameter_mm",
            bore_diameter_mm,
            minimum=0.0,
        )
        if internal:
            if clean_bore != 0.0:
                raise _error(
                    operation,
                    "bore_diameter_mm",
                    "must be zero for an internal gear",
                    bore_diameter_mm,
                )
            if outer_diameter_mm is None:
                raise _error(
                    operation,
                    "outer_diameter_mm",
                    "is required for an internal gear",
                )
        elif outer_diameter_mm is not None:
            raise _error(
                operation,
                "outer_diameter_mm",
                "applies only to an internal gear",
                outer_diameter_mm,
            )
        addendum = (
            0.6
            if addendum_coefficient is None and internal
            else 1.0
            if addendum_coefficient is None
            else _number(
                operation,
                "addendum_coefficient",
                addendum_coefficient,
                minimum=0.0,
                strict=True,
            )
        )
        pressure_angle = _number(
            operation,
            "pressure_angle_degrees",
            pressure_angle_degrees,
            minimum=0.0,
            strict=True,
        )
        if pressure_angle >= 90.0:
            raise _error(
                operation,
                "pressure_angle_degrees",
                "must be less than 90",
                pressure_angle_degrees,
            )
        return self._graph(
            operation,
            "feature",
            _integer(operation, "teeth", teeth, minimum=3),
            _number(operation, "module_mm", module_mm, minimum=0.0, strict=True),
            _number(operation, "width_mm", width_mm, minimum=0.0, strict=True),
            pressure_angle_degrees=pressure_angle,
            bore_diameter_mm=clean_bore,
            internal=internal,
            outer_diameter_mm=(
                None
                if outer_diameter_mm is None
                else _number(
                    operation,
                    "outer_diameter_mm",
                    outer_diameter_mm,
                    minimum=0.0,
                    strict=True,
                )
            ),
            high_precision=high_precision,
            addendum_coefficient=addendum,
            dedendum_coefficient=_number(
                operation,
                "dedendum_coefficient",
                dedendum_coefficient,
                minimum=0.0,
                strict=True,
            ),
            root_fillet_coefficient=_number(
                operation,
                "root_fillet_coefficient",
                root_fillet_coefficient,
                minimum=0.0,
            ),
            profile_shift_coefficient=_number(
                operation,
                "profile_shift_coefficient",
                profile_shift_coefficient,
            ),
            label=_label(operation, label),
        )

    def point(self, position: Sequence[float], *, construction: bool = True, name: str = "") -> DomainValue:
        """Sketch point at [u,v]; construction defaults true."""

        return self._from_sketcher(
            self._sketcher.point(position, construction=construction, name=name)
        )

    def line(
        self,
        start: Sequence[float],
        end: Sequence[float],
        *,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Sketch line from start [u,v] to end [u,v]."""

        return self._from_sketcher(
            self._sketcher.line(
                start,
                end,
                construction=construction,
                name=name,
            )
        )

    def arc(
        self,
        start: Sequence[float],
        through: Sequence[float],
        end: Sequence[float],
        *,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Sketch arc traveling start -> through -> end through three [u,v] points."""

        return self._from_sketcher(
            self._sketcher.arc(start, through, end, construction=construction, name=name),
        )

    def circle(
        self,
        center: Sequence[float],
        radius: float,
        *,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Full sketch circle at center [u,v]."""

        return self._from_sketcher(
            self._sketcher.circle(center, radius, construction=construction, name=name),
        )

    def ellipse(
        self,
        center: Sequence[float],
        major_radius: float,
        minor_radius: float,
        *,
        rotation_degrees: float = 0.0,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Full sketch ellipse; rotation_degrees sets its major-axis angle."""

        return self._from_sketcher(
            self._sketcher.ellipse(
                center,
                major_radius,
                minor_radius,
                rotation_degrees=rotation_degrees,
                construction=construction,
                name=name,
            ),
        )

    def bspline(
        self,
        points: Sequence[Sequence[float]],
        *,
        degree: int | None = None,
        knots: Sequence[float] = (),
        multiplicities: Sequence[int] = (),
        weights: Sequence[float] = (),
        periodic: bool = False,
        tolerance: float = 1.0e-7,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Sketch B-spline through points, or exact NURBS when knot data is supplied."""

        return self._from_sketcher(
            self._sketcher.bspline(
                points,
                degree=degree,
                knots=knots,
                multiplicities=multiplicities,
                weights=weights,
                periodic=periodic,
                tolerance=tolerance,
                construction=construction,
                name=name,
            ),
        )

    def external_geometry(
        self,
        reference: Mapping[str, Any],
        selection: Mapping[str, Any] | str,
        *,
        defining: bool = False,
        intersection: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Project one stable referenced edge or vertex into a sketch.

        reference must come from a validated document-reference input; selection
        identifies the published interface or exact source subelement.
        """

        return self._from_sketcher(
            self._sketcher.external_geometry(
                reference,
                selection,
                defining=defining,
                intersection=intersection,
                name=name,
            ),
        )

    def constraint(
        self,
        kind: str,
        entities: Sequence[Any],
        *,
        value: float | None = None,
        name: str = "",
        expression: str = "",
        driving: bool = True,
        active: bool = True,
        virtual: bool = False,
        alignment: str = "",
        internal_index: int = 0,
        text: str = "",
        font: str = "sans",
        text_height: bool = True,
    ) -> DomainValue:
        """Create one Sketcher constraint for geometry used by the same api.sketch.

        entities contains geometry values or their point selectors. Dimensional
        constraint kinds require value; name gives the dimension a stable identity.
        """

        sketcher_entities = self._to_sketcher(
            entities,
            operation="constraint",
            parameter="entities",
        )
        return self._from_sketcher(
            self._sketcher.constraint(
                kind,
                sketcher_entities,
                value=value,
                name=name,
                expression=expression,
                driving=driving,
                active=active,
                virtual=virtual,
                alignment=alignment,
                internal_index=internal_index,
                text=text,
                font=font,
                text_height=text_height,
            ),
        )

    def sketch(
        self,
        geometry: Sequence[DomainValue],
        constraints: Sequence[DomainValue] = (),
        *,
        plane: str = "XY",
        z_offset_mm: float = 0.0,
        plane_offset_mm: float | None = None,
        placement: Mapping[str, Sequence[float]] | None = None,
        support: Mapping[str, Any] | None = None,
        map_mode: str | None = None,
        attachment_offset: Mapping[str, Any] | None = None,
        require_fully_constrained: bool = False,
        require_closed_profile: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Create a planar profile, not a solid.

        XY maps [u,v] to [X,Y] with normal +Z.
        XZ maps [u,v] to [X,Z] with normal -Y.
        YZ maps [u,v] to [Y,Z] with normal +X.
        plane_offset_mm follows the normal and excludes z_offset_mm. placement defines
        an unattached arbitrary plane; support requires map_mode. require_* rejects an
        invalid profile.
        """

        clean_z_offset = _number("sketch", "z_offset_mm", z_offset_mm)
        clean_plane_offset = (
            clean_z_offset
            if plane_offset_mm is None
            else _number("sketch", "plane_offset_mm", plane_offset_mm)
        )
        if plane_offset_mm is not None and abs(clean_z_offset) > 1.0e-12:
            raise _error(
                "sketch",
                "plane_offset_mm/z_offset_mm",
                "specify one offset name, not both",
            )
        clean_placement = _sketch_placement(placement)
        if clean_placement is not None and (
            support is not None
            or attachment_offset is not None
            or abs(clean_plane_offset) > 1.0e-12
        ):
            raise _error(
                "sketch",
                "placement",
                "cannot be combined with support, attachment_offset, or a plane offset",
            )
        if support is None and attachment_offset is not None:
            raise _error(
                "sketch",
                "attachment_offset",
                "requires support; use placement for an unattached arbitrary plane",
            )
        if support is not None and map_mode is None:
            raise _error(
                "sketch",
                "map_mode",
                "is required with support; use the exact native mode for that support",
            )
        if support is None and map_mode is not None:
            raise _error(
                "sketch",
                "map_mode",
                "requires support; omit it for a principal or explicitly placed plane",
            )
        clean_map_mode = str(map_mode or "Deactivated").strip()

        value = self._sketcher.sketch(
            self._to_sketcher(
                geometry,
                operation="sketch",
                parameter="geometry",
            ),
            self._to_sketcher(
                constraints,
                operation="sketch",
                parameter="constraints",
            ),
            support=support,
            map_mode=clean_map_mode,
            attachment_offset=attachment_offset,
            require_fully_constrained=require_fully_constrained,
            require_closed_profile=require_closed_profile,
            label=label,
        )
        retagged = _retag(value, "partdesign")
        return DomainValue(
            domain="partdesign",
            operation="sketch",
            output_type="profile",
            arguments=retagged.arguments,
            properties={
                **dict(retagged.properties),
                "graph_id": self._feature_id(),
                "plane": _plane("sketch", plane),
                "z_offset_mm": clean_plane_offset,
                "plane_offset_mm": clean_plane_offset,
                "placement": clean_placement,
            },
        )

    def _pad_feature(
        self,
        profile: DomainValue,
        length_mm: float,
        *,
        base: DomainValue | None,
        reverse: bool,
        midplane: bool,
        direction: str | None,
        refine: bool,
        label: str,
        api_operation: str,
    ) -> DomainValue:
        clean_reverse, clean_midplane, clean_direction = _linear_feature_direction(
            api_operation,
            direction,
            reverse=reverse,
            midplane=midplane,
            subtractive=False,
        )
        return self._graph(
            "pad",
            "feature",
            _profile(api_operation, "profile", profile),
            _number(
                api_operation,
                "length_mm",
                length_mm,
                minimum=0.0,
                strict=True,
            ),
            base=(
                None
                if base is None
                else _feature(api_operation, "base", base)
            ),
            reverse=clean_reverse,
            midplane=clean_midplane,
            direction=clean_direction,
            refine=bool(refine),
            label=_label(api_operation, label),
        )

    def extrude(
        self,
        profile: DomainValue,
        distance_mm: float | None = None,
        *,
        operation: str = "add_material",
        base: DomainValue | None = None,
        through_all: bool = False,
        reverse: bool = False,
        midplane: bool = False,
        direction: str | None = None,
        refine: bool = True,
        vector: Sequence[float] | None = None,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Extrude when the cross-section stays constant.

        Body features use add_material/remove_material and base after the first
        addition. direction has the same meaning for additions and cuts:
        along_normal, opposite_normal, or symmetric. Cuts need distance_mm or
        through_all. Standalone new_solid/new_surface always need distance_mm;
        vector optionally overrides the profile normal and is normalized, not a
        displacement. Omit output_type; it is inferred from the source.
        """

        intent = _operation_intent("extrude", operation, allow_creation=True)
        if intent == "add_material":
            clean_profile = _profile("extrude", "profile", profile)
            if through_all:
                raise _error(
                    "extrude",
                    "through_all",
                    "is available only when operation='remove_material'",
                )
            if distance_mm is None:
                raise _error("extrude", "distance_mm", "is required to add material")
            if vector is not None or output_type is not None:
                raise _error(
                    "extrude",
                    "vector/output_type",
                    "are available only for new standalone geometry",
                )
            return self._pad_feature(
                clean_profile,
                distance_mm,
                base=base,
                reverse=reverse,
                midplane=midplane,
                direction=direction,
                refine=refine,
                label=label,
                api_operation="extrude",
            )
        if intent == "remove_material":
            clean_profile = _profile("extrude", "profile", profile)
            if base is None:
                raise _error("extrude", "base", "is required to remove material")
            if vector is not None or output_type is not None:
                raise _error(
                    "extrude",
                    "vector/output_type",
                    "are available only for new standalone geometry",
                )
            return self._pocket_feature(
                base,
                clean_profile,
                distance_mm,
                through_all=through_all,
                reverse=reverse,
                midplane=midplane,
                direction=direction,
                refine=refine,
                label=label,
                api_operation="extrude",
            )
        if base is not None or through_all:
            raise _error(
                "extrude",
                "base/through_all",
                "are Body-material settings and cannot create standalone geometry",
            )
        if direction is not None:
            raise _error(
                "extrude",
                "direction",
                "is for Body features; use vector for standalone geometry",
            )
        if distance_mm is None:
            raise _error("extrude", "distance_mm", "is required for standalone geometry")
        source = _value(
            profile,
            {"profile", "edge", "wire", "face"},
            "profile",
            "extrude",
        )
        if source.output_type in {"edge", "wire"} and vector is None:
            raise _error(
                "extrude",
                "vector",
                "is required when extruding a standalone edge or wire",
            )
        if intent == "new_solid" and source.output_type not in {"profile", "face"}:
            raise _error(
                "extrude",
                "profile",
                "must be a closed profile or face when operation='new_solid'",
                source.output_type,
            )
        inferred = (
            "solid"
            if intent == "new_solid"
            else "face"
            if source.output_type == "edge"
            else "shell"
        )
        declared = inferred if output_type is None else _publishable_type("extrude", output_type)
        if declared != inferred:
            raise _error("extrude", "output_type", f"must be {inferred!r} for this source")
        return self._graph(
            "standalone_extrude",
            inferred,
            source,
            _number("extrude", "distance_mm", distance_mm, minimum=0.0, strict=True),
            vector=(
                None
                if vector is None
                else _nonzero_vector("extrude", "vector", vector)
            ),
            reverse=bool(reverse),
            midplane=bool(midplane),
            refine=bool(refine),
            label=_label("extrude", label),
        )

    def _pocket_feature(
        self,
        base: DomainValue,
        profile: DomainValue,
        length_mm: float | None,
        *,
        through_all: bool,
        reverse: bool,
        midplane: bool,
        direction: str | None,
        refine: bool,
        label: str,
        api_operation: str,
    ) -> DomainValue:
        if through_all == (length_mm is not None):
            raise _error(
                api_operation,
                "length_mm/through_all",
                "must provide exactly one of a positive length or through_all=True",
            )
        length = None if length_mm is None else _number(
            api_operation,
            "length_mm",
            length_mm,
            minimum=0.0,
            strict=True,
        )
        clean_reverse, clean_midplane, clean_direction = _linear_feature_direction(
            api_operation,
            direction,
            reverse=reverse,
            midplane=midplane,
            subtractive=True,
        )
        return self._graph(
            "pocket",
            "feature",
            _feature(api_operation, "base", base),
            _profile(api_operation, "profile", profile),
            length,
            through_all=bool(through_all),
            reverse=clean_reverse,
            midplane=clean_midplane,
            direction=clean_direction,
            refine=bool(refine),
            label=_label(api_operation, label),
        )

    def revolve(
        self,
        profile: DomainValue,
        angle_degrees: float = 360.0,
        *,
        operation: str = "add_material",
        base: DomainValue | None = None,
        axis: str = "V",
        axis_origin: Sequence[float] = (0.0, 0.0, 0.0),
        axis_direction: Sequence[float] | None = None,
        reverse: bool = False,
        midplane: bool = False,
        refine: bool = True,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create an axial feature or standalone revolution from a profile.

        operation is add_material, remove_material, new_solid, or new_surface.
        remove_material requires base. Body features use axis H, V, N, X, Y, or Z;
        axis_origin, axis_direction, and output_type apply only to standalone geometry.
        """

        angle = _number("revolve", "angle_degrees", angle_degrees, minimum=0.0, strict=True)
        if angle > 360.0:
            raise _error("revolve", "angle_degrees", "must not exceed 360", angle)
        intent = _operation_intent("revolve", operation, allow_creation=True)
        clean_axis_origin = _vector("revolve", "axis_origin", axis_origin)
        if intent == "remove_material" and base is None:
            raise _error("revolve", "base", "is required to remove material")
        if intent in _MATERIAL_OPERATIONS:
            if (
                axis_direction is not None
                or output_type is not None
                or any(abs(item) > 1.0e-12 for item in clean_axis_origin)
            ):
                raise _error(
                    "revolve",
                    "axis_origin/axis_direction/output_type",
                    "are available only for new standalone geometry",
                )
            clean_profile = _profile("revolve", "profile", profile)
        if intent == "remove_material":
            return self._graph(
                "groove",
                "feature",
                _feature("revolve", "base", base),
                clean_profile,
                angle,
                axis=_axis("revolve", axis),
                reverse=bool(reverse),
                midplane=bool(midplane),
                refine=bool(refine),
                label=_label("revolve", label),
            )
        if intent == "add_material":
            return self._graph(
                "revolve",
                "feature",
                clean_profile,
                angle,
                base=None if base is None else _feature("revolve", "base", base),
                axis=_axis("revolve", axis),
                reverse=bool(reverse),
                midplane=bool(midplane),
                refine=bool(refine),
                label=_label("revolve", label),
            )
        if base is not None:
            raise _error("revolve", "base", "cannot be used for standalone geometry")
        source = _value(
            profile,
            {"profile", "edge", "wire", "face"},
            "profile",
            "revolve",
        )
        if intent == "new_solid" and source.output_type not in {"profile", "face"}:
            raise _error(
                "revolve",
                "profile",
                "must be a closed profile or face when operation='new_solid'",
            )
        inferred = (
            "solid"
            if intent == "new_solid"
            else "face"
            if source.output_type == "edge"
            else "shell"
        )
        declared = inferred if output_type is None else _publishable_type("revolve", output_type)
        if declared != inferred:
            raise _error("revolve", "output_type", f"must be {inferred!r} for this source")
        return self._graph(
            "standalone_revolve",
            inferred,
            source,
            angle,
            axis=_axis("revolve", axis),
            axis_origin=clean_axis_origin,
            axis_direction=(
                None
                if axis_direction is None
                else _nonzero_vector("revolve", "axis_direction", axis_direction)
            ),
            reverse=bool(reverse),
            midplane=bool(midplane),
            refine=bool(refine),
            label=_label("revolve", label),
        )

    def loft(
        self,
        sections: Sequence[DomainValue],
        *,
        base: DomainValue | None = None,
        operation: str = "add_material",
        ruled: bool = False,
        closed: bool = False,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Create a loft through 2-64 ordered sections.

        Use this only when the intended cross-section genuinely changes between
        sections. For a constant cross-section, use api.extrude.
        Use api.sketch sections for planar profiles. operation selects add_material,
        remove_material, new_solid, or new_surface; remove_material requires base.
        Direct wire sections are valid only for standalone nonplanar topology.
        """

        if not isinstance(sections, (list, tuple)) or not 2 <= len(sections) <= 64:
            raise _error("loft", "sections", "must contain 2-64 profile values")
        clean_sections = list(sections)
        intent = _operation_intent("loft", operation, allow_creation=True)
        if intent in _CREATION_OPERATIONS:
            if base is not None:
                raise _error("loft", "base", "cannot be used for standalone geometry")
            standalone_sections = [
                _value(
                    item,
                    {"profile", "wire"},
                    f"sections[{index}]",
                    "loft",
                )
                for index, item in enumerate(clean_sections)
            ]
            return self._graph(
                "standalone_loft",
                "solid" if intent == "new_solid" else "shell",
                standalone_sections,
                solid=intent == "new_solid",
                ruled=bool(ruled),
                closed=bool(closed),
                refine=bool(refine),
                label=_label("loft", label),
            )
        clean_sections = [
            _profile("loft", f"sections[{index}]", item)
            for index, item in enumerate(clean_sections)
        ]
        clean_base = None if base is None else _feature("loft", "base", base)
        is_subtractive = intent == "remove_material"
        if is_subtractive and clean_base is None:
            raise _error("loft", "base", "is required for a subtractive loft")
        return self._graph(
            "loft",
            "feature",
            clean_sections,
            base=clean_base,
            subtractive=is_subtractive,
            ruled=bool(ruled),
            closed=bool(closed),
            refine=bool(refine),
            label=_label("loft", label),
        )

    def sweep(
        self,
        profile: DomainValue | Sequence[DomainValue],
        path: DomainValue,
        *,
        operation: str = "new_solid",
        base: DomainValue | None = None,
        frenet: bool = False,
        transition: str = "transformed",
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Sweep one or more ordered profiles along one edge or wire path.

        operation is add_material, remove_material, new_solid, or new_surface.
        remove_material requires base; new geometry forbids base. Use api.sketch for
        planar profiles and a direct edge or wire only for the spatial path.
        """

        intent = _operation_intent("sweep", operation, allow_creation=True)
        raw_profiles = [profile] if isinstance(profile, DomainValue) else list(profile)
        if not 1 <= len(raw_profiles) <= 64:
            raise _error("sweep", "profile", "must contain 1-64 ordered profiles")
        profiles = [
            _value(item, {"profile", "wire"}, f"profile[{index}]", "sweep")
            for index, item in enumerate(raw_profiles)
        ]
        clean_path = _topology(
            "sweep", "path", path, allowed={"edge", "wire"}
        )
        clean_transition = str(transition or "").strip().lower()
        if clean_transition not in {"transformed", "right_corner", "round_corner"}:
            raise _error(
                "sweep",
                "transition",
                "must be transformed, right_corner, or round_corner",
                transition,
            )
        if intent in _CREATION_OPERATIONS:
            if base is not None:
                raise _error("sweep", "base", "cannot be used for standalone geometry")
            return self._graph(
                "standalone_sweep",
                "solid" if intent == "new_solid" else "shell",
                profiles,
                clean_path,
                solid=intent == "new_solid",
                frenet=bool(frenet),
                transition=clean_transition,
                refine=bool(refine),
                label=_label("sweep", label),
            )
        if intent == "remove_material" and base is None:
            raise _error("sweep", "base", "is required to remove material")
        return self._graph(
            "material_sweep",
            "feature",
            profiles,
            clean_path,
            base=None if base is None else _feature("sweep", "base", base),
            subtractive=intent == "remove_material",
            frenet=bool(frenet),
            transition=clean_transition,
            refine=bool(refine),
            label=_label("sweep", label),
        )

    def helix(
        self,
        profile: DomainValue,
        *,
        operation: str,
        pitch_mm: float,
        height_mm: float,
        radius_mm: float,
        base: DomainValue | None = None,
        left_handed: bool = False,
        reversed: bool = False,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Add or remove Body material by sweeping a closed profile on a helix.

        operation is add_material or remove_material; removal requires base.
        pitch_mm, height_mm, and radius_mm are positive and define the helical path.
        """

        intent = _operation_intent("helix", operation, allow_creation=False)
        if intent == "remove_material" and base is None:
            raise _error("helix", "base", "is required to remove material")
        return self._graph(
            "material_helix",
            "feature",
            _profile("helix", "profile", profile),
            _number("helix", "pitch_mm", pitch_mm, minimum=0.0, strict=True),
            _number("helix", "height_mm", height_mm, minimum=0.0, strict=True),
            _number("helix", "radius_mm", radius_mm, minimum=0.0, strict=True),
            base=None if base is None else _feature("helix", "base", base),
            subtractive=intent == "remove_material",
            left_handed=bool(left_handed),
            reversed=bool(reversed),
            refine=bool(refine),
            label=_label("helix", label),
        )

    def boolean(
        self,
        shapes: Sequence[DomainValue],
        *,
        operation: str,
        output_type: str = "solid",
        tolerance_mm: float = 0.0,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Union/intersect all shapes; subtract uses the first as base. A solid union needs positive-volume overlap at each operand; face/edge contact is invalid. compound permits separate pieces."""

        intent = str(operation or "").strip().lower()
        if intent not in {"union", "subtract", "intersect"}:
            raise _error(
                "boolean", "operation", "must be union, subtract, or intersect", operation
            )
        if not isinstance(shapes, (list, tuple)) or len(shapes) < 2:
            raise _error("boolean", "shapes", "must contain at least two modeled values")
        clean_shapes = [
            _modeled("boolean", f"shapes[{index}]", item)
            for index, item in enumerate(shapes)
        ]
        clean_type = _publishable_type("boolean", output_type)
        if clean_type not in {"solid", "compound"}:
            raise _error("boolean", "output_type", "must be 'solid' or 'compound'")
        if clean_type == "solid" and any(
            item.output_type not in {"feature", "solid"} for item in clean_shapes
        ):
            raise _error(
                "boolean",
                "shapes",
                "must all be solids or Body features when output_type='solid'",
            )
        return self._graph(
            "boolean",
            clean_type,
            clean_shapes,
            boolean_operation=intent,
            tolerance_mm=_number(
                "boolean", "tolerance_mm", tolerance_mm, minimum=0.0
            ),
            refine=bool(refine),
            label=_label("boolean", label),
        )

    def union(
        self,
        shapes: Sequence[DomainValue],
        *,
        output_type: str = "solid",
        tolerance_mm: float = 0.0,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Fuse solids. A solid result needs positive-volume overlap at each operand; face/edge contact is invalid. compound permits separate pieces."""

        return self.boolean(
            shapes,
            operation="union",
            output_type=output_type,
            tolerance_mm=tolerance_mm,
            refine=refine,
            label=label,
        )

    def cut(
        self,
        base: DomainValue,
        tools: DomainValue | Sequence[DomainValue],
        *,
        output_type: str = "solid",
        tolerance_mm: float = 0.0,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Subtract one tool or an array of tools from base."""

        if isinstance(tools, DomainValue):
            clean_tools = [tools]
        elif isinstance(tools, (list, tuple)):
            clean_tools = list(tools)
        else:
            raise _error(
                "cut",
                "tools",
                "must be one modeled value or an array of modeled values",
                tools,
            )
        if not clean_tools:
            raise _error("cut", "tools", "must contain at least one modeled value")
        return self.boolean(
            [base, *clean_tools],
            operation="subtract",
            output_type=output_type,
            tolerance_mm=tolerance_mm,
            refine=refine,
            label=label,
        )

    def intersect(
        self,
        shapes: Sequence[DomainValue],
        *,
        output_type: str = "solid",
        tolerance_mm: float = 0.0,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Keep the volume shared by every supplied solid."""

        return self.boolean(
            shapes,
            operation="intersect",
            output_type=output_type,
            tolerance_mm=tolerance_mm,
            refine=refine,
            label=label,
        )

    def compound(
        self,
        shapes: Sequence[DomainValue],
        *,
        label: str = "",
    ) -> DomainValue:
        """Return disconnected modeled shapes as one compound without fusing them."""

        if not isinstance(shapes, (list, tuple)) or not 1 <= len(shapes) <= 1024:
            raise _error("compound", "shapes", "must contain 1-1024 modeled values")
        return self._graph(
            "model_compound",
            "compound",
            [
                _modeled("compound", f"shapes[{index}]", item)
                for index, item in enumerate(shapes)
            ],
            label=_label("compound", label),
        )

    def polar_pattern(
        self,
        base: DomainValue,
        occurrences: int,
        *,
        axis: str = "N",
        angle_degrees: float = 360.0,
        center: Sequence[float] = (0.0, 0.0, 0.0),
        axis_direction: Sequence[float] | None = None,
        result: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Repeat a feature or standalone shape around an axis.

        A feature returns one additive Body feature. A standalone shape returns a
        compound by default; result='union' requires copies that form one solid.
        """

        angle = _number(
            "polar_pattern", "angle_degrees", angle_degrees, minimum=0.0, strict=True
        )
        if angle > 360.0:
            raise _error("polar_pattern", "angle_degrees", "must not exceed 360", angle)
        count = _integer("polar_pattern", "occurrences", occurrences, minimum=2)
        clean_center = _vector("polar_pattern", "center", center)
        requested_result = (
            None if result is None else str(result or "").strip().lower()
        )
        if isinstance(base, DomainValue) and base.output_type == "feature":
            if (
                axis_direction is not None
                or any(abs(item) > 1.0e-12 for item in clean_center)
                or requested_result not in {None, "union"}
            ):
                raise _error(
                    "polar_pattern",
                    "center/axis_direction/result",
                    "are standalone-shape settings",
                )
            return self._graph(
                "polar_pattern",
                "feature",
                _feature("polar_pattern", "base", base),
                count,
                axis=_axis("polar_pattern", axis),
                angle_degrees=angle,
                label=_label("polar_pattern", label),
            )
        clean_base = _topology("polar_pattern", "base", base)
        clean_result = "compound" if requested_result is None else requested_result
        if clean_result not in {"compound", "union"}:
            raise _error("polar_pattern", "result", "must be compound or union", result)
        if clean_result == "union" and clean_base.output_type != "solid":
            raise _error(
                "polar_pattern",
                "base",
                "must be a solid when result='union'",
                clean_base.output_type,
            )
        return self._graph(
            "standalone_polar_pattern",
            "solid" if clean_result == "union" else "compound",
            clean_base,
            count,
            center=clean_center,
            axis_direction=(
                _nonzero_vector("polar_pattern", "axis_direction", axis_direction)
                if axis_direction is not None
                else {
                    "H": [1.0, 0.0, 0.0],
                    "X": [1.0, 0.0, 0.0],
                    "V": [0.0, 1.0, 0.0],
                    "Y": [0.0, 1.0, 0.0],
                    "N": [0.0, 0.0, 1.0],
                    "Z": [0.0, 0.0, 1.0],
                }[_axis("polar_pattern", axis)]
            ),
            angle_degrees=angle,
            result=clean_result,
            label=_label("polar_pattern", label),
        )

    def linear_pattern(
        self,
        base: DomainValue,
        occurrences: int,
        distance_mm: float,
        *,
        direction: Sequence[float] = (1.0, 0.0, 0.0),
        result: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Repeat geometry along direction over total distance_mm.

        A Body feature uses result='union'. Standalone copies default to a compound;
        request result='union' only when all copies form one connected solid.
        """

        clean_base = _modeled("linear_pattern", "base", base)
        clean_result = (
            "union"
            if result is None and clean_base.output_type == "feature"
            else "compound"
            if result is None
            else str(result or "").strip().lower()
        )
        if clean_result not in {"compound", "union"}:
            raise _error("linear_pattern", "result", "must be compound or union", result)
        if clean_base.output_type == "feature" and clean_result != "union":
            raise _error(
                "linear_pattern",
                "result",
                "must be 'union' for a Body feature",
                result,
            )
        if (
            clean_result == "union"
            and clean_base.output_type not in {"feature", "solid"}
        ):
            raise _error(
                "linear_pattern",
                "base",
                "must be a solid or Body feature when result='union'",
                clean_base.output_type,
            )
        return self._graph(
            "linear_pattern",
            "feature" if clean_base.output_type == "feature" else (
                "solid" if clean_result == "union" else "compound"
            ),
            clean_base,
            _integer("linear_pattern", "occurrences", occurrences, minimum=2),
            _number(
                "linear_pattern", "distance_mm", distance_mm, minimum=0.0, strict=True
            ),
            direction=_nonzero_vector("linear_pattern", "direction", direction),
            result=clean_result,
            label=_label("linear_pattern", label),
        )

    def multi_transform(
        self,
        base: DomainValue,
        transformations: Sequence[Mapping[str, Any]],
        *,
        result: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Apply 2-32 explicit translate, rotate, mirror, or scale pattern steps.

        A Body feature requires result='union'; standalone results may be a compound
        or one connected union.
        """

        if not isinstance(transformations, (list, tuple)) or not 2 <= len(transformations) <= 32:
            raise _error(
                "multi_transform", "transformations", "must contain 2-32 steps"
            )
        clean_steps: list[dict[str, Any]] = []
        for index, raw in enumerate(transformations):
            if not isinstance(raw, Mapping):
                raise _error(
                    "multi_transform", f"transformations[{index}]", "must be an object"
                )
            step = {str(key): value for key, value in raw.items()}
            kind = str(step.get("type") or "").strip().lower()
            if kind not in {"translate", "rotate", "mirror", "scale"}:
                raise _error(
                    "multi_transform",
                    f"transformations[{index}].type",
                    "must be translate, rotate, mirror, or scale",
                    kind,
                )
            context = f"transformations[{index}]"
            if kind == "translate":
                if set(step) != {"type", "vector"}:
                    raise _error(
                        "multi_transform",
                        context,
                        "translate must contain exactly type and vector",
                    )
                clean_step = {
                    "type": kind,
                    "vector": _nonzero_vector(
                        "multi_transform", f"{context}.vector", step["vector"]
                    ),
                }
            elif kind == "rotate":
                if not set(step) <= {"type", "origin", "axis", "angle_degrees"} or not {
                    "type",
                    "axis",
                    "angle_degrees",
                } <= set(step):
                    raise _error(
                        "multi_transform",
                        context,
                        "rotate requires type, axis, and angle_degrees; origin is optional",
                    )
                angle = _number(
                    "multi_transform",
                    f"{context}.angle_degrees",
                    step["angle_degrees"],
                    minimum=0.0,
                    strict=True,
                )
                if angle > 360.0:
                    raise _error(
                        "multi_transform",
                        f"{context}.angle_degrees",
                        "must not exceed 360",
                        angle,
                    )
                clean_step = {
                    "type": kind,
                    "origin": _vector(
                        "multi_transform",
                        f"{context}.origin",
                        step.get("origin", (0.0, 0.0, 0.0)),
                    ),
                    "axis": _nonzero_vector(
                        "multi_transform", f"{context}.axis", step["axis"]
                    ),
                    "angle_degrees": angle,
                }
            elif kind == "mirror":
                if not set(step) <= {"type", "origin", "normal"} or "normal" not in step:
                    raise _error(
                        "multi_transform",
                        context,
                        "mirror requires type and normal; origin is optional",
                    )
                clean_step = {
                    "type": kind,
                    "origin": _vector(
                        "multi_transform",
                        f"{context}.origin",
                        step.get("origin", (0.0, 0.0, 0.0)),
                    ),
                    "normal": _nonzero_vector(
                        "multi_transform", f"{context}.normal", step["normal"]
                    ),
                }
            else:
                if not set(step) <= {"type", "center", "factor"} or "factor" not in step:
                    raise _error(
                        "multi_transform",
                        context,
                        "scale requires type and factor; center is optional",
                    )
                clean_step = {
                    "type": kind,
                    "center": _vector(
                        "multi_transform",
                        f"{context}.center",
                        step.get("center", (0.0, 0.0, 0.0)),
                    ),
                    "factor": _number(
                        "multi_transform",
                        f"{context}.factor",
                        step["factor"],
                        minimum=0.0,
                        strict=True,
                    ),
                }
            clean_steps.append(clean_step)
        clean_base = _modeled("multi_transform", "base", base)
        clean_result = (
            "union"
            if result is None and clean_base.output_type == "feature"
            else "compound"
            if result is None
            else str(result or "").strip().lower()
        )
        if clean_result not in {"compound", "union"}:
            raise _error("multi_transform", "result", "must be compound or union", result)
        if clean_base.output_type == "feature" and clean_result != "union":
            raise _error(
                "multi_transform",
                "result",
                "must be 'union' for a Body feature",
                result,
            )
        if (
            clean_result == "union"
            and clean_base.output_type not in {"feature", "solid"}
        ):
            raise _error(
                "multi_transform",
                "base",
                "must be a solid or Body feature when result='union'",
                clean_base.output_type,
            )
        return self._graph(
            "multi_transform",
            "feature" if clean_base.output_type == "feature" else (
                "solid" if clean_result == "union" else "compound"
            ),
            clean_base,
            clean_steps,
            result=clean_result,
            label=_label("multi_transform", label),
        )

    def mirror(
        self,
        base: DomainValue,
        plane: str = "YZ",
        *,
        plane_origin: Sequence[float] = (0.0, 0.0, 0.0),
        plane_normal: Sequence[float] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Mirror a feature across XY, XZ, or YZ, or a standalone shape across any plane.

        plane_origin and plane_normal are valid only for standalone geometry.
        """

        clean_origin = _vector("mirror", "plane_origin", plane_origin)
        if isinstance(base, DomainValue) and base.output_type == "feature":
            if plane_normal is not None or any(
                abs(item) > 1.0e-12 for item in clean_origin
            ):
                raise _error(
                    "mirror",
                    "plane_origin/plane_normal",
                    "are available only for standalone shapes",
                )
            return self._graph(
                "mirror",
                "feature",
                _feature("mirror", "base", base),
                plane=_plane("mirror", plane),
                label=_label("mirror", label),
            )
        clean_base = _topology("mirror", "base", base)
        normal = (
            _nonzero_vector("mirror", "plane_normal", plane_normal)
            if plane_normal is not None
            else {
                "XY": [0.0, 0.0, 1.0],
                "XZ": [0.0, 1.0, 0.0],
                "YZ": [1.0, 0.0, 0.0],
            }[_plane("mirror", plane)]
        )
        return self._graph(
            "standalone_mirror",
            clean_base.output_type,
            clean_base,
            clean_origin,
            normal,
            label=_label("mirror", label),
        )

    def fillet(
        self,
        base: DomainValue,
        selection: Mapping[str, Any],
        radius_mm: float,
        *,
        label: str = "",
    ) -> DomainValue:
        """Round exact edges; pass selection=api.find_subelements(element_type="edge", expected_count=..., near_point=[x,y,z], ...), using enough filters to identify only the intended edges."""

        clean_base = _modeled(
            "fillet", "base", base, topology={"solid", "shell"}
        )
        return self._graph(
            "fillet" if clean_base.output_type == "feature" else "model_fillet",
            "feature" if clean_base.output_type == "feature" else clean_base.output_type,
            clean_base,
            _selection("fillet", selection, element_type="edge", allow_all_edges=True),
            _number("fillet", "radius_mm", radius_mm, minimum=0.0, strict=True),
            label=_label("fillet", label),
        )

    def chamfer(
        self,
        base: DomainValue,
        selection: Mapping[str, Any],
        size_mm: float,
        *,
        label: str = "",
    ) -> DomainValue:
        """Bevel exact edges; pass selection=api.find_subelements(element_type="edge", expected_count=..., near_point=[x,y,z], ...), using enough filters to identify only the intended edges."""

        clean_base = _modeled(
            "chamfer", "base", base, topology={"solid", "shell"}
        )
        return self._graph(
            "chamfer" if clean_base.output_type == "feature" else "model_chamfer",
            "feature" if clean_base.output_type == "feature" else clean_base.output_type,
            clean_base,
            _selection("chamfer", selection, element_type="edge", allow_all_edges=True),
            _number("chamfer", "size_mm", size_mm, minimum=0.0, strict=True),
            label=_label("chamfer", label),
        )

    def thickness(
        self,
        base: DomainValue,
        selection: Mapping[str, Any],
        thickness_mm: float,
        *,
        inward: bool = False,
        join: str = "arc",
        label: str = "",
    ) -> DomainValue:
        """Remove selected faces and offset the remainder to make a wall thickness."""

        clean_base = _modeled(
            "thickness", "base", base, topology={"solid", "shell"}
        )
        clean_join = str(join or "").strip().lower()
        if clean_join not in {"arc", "tangent", "intersection"}:
            raise _error(
                "thickness", "join", "must be arc, tangent, or intersection", join
            )
        return self._graph(
            "thickness" if clean_base.output_type == "feature" else "model_thickness",
            "feature" if clean_base.output_type == "feature" else "solid",
            clean_base,
            _selection("thickness", selection, element_type="face"),
            _number(
                "thickness", "thickness_mm", thickness_mm, minimum=0.0, strict=True
            ),
            inward=bool(inward),
            join=clean_join,
            label=_label("thickness", label),
        )

    def move_planar_faces(
        self,
        base: DomainValue,
        selection: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        distance_mm: float,
        *,
        label: str = "",
    ) -> DomainValue:
        """Move selected planar faces along their outward normals; positive grows the solid, negative removes material. Pass one exact face query or a list of queries."""

        clean_base = _topology(
            "move_planar_faces", "base", base, allowed={"solid"}
        )
        raw_selections = (
            [selection] if isinstance(selection, Mapping) else selection
        )
        if (
            not isinstance(raw_selections, (list, tuple))
            or not raw_selections
            or len(raw_selections) > 64
        ):
            raise _error(
                "move_planar_faces",
                "selection",
                "must be one face query or a list of 1 to 64 face queries",
            )
        selections = [
            _selection("move_planar_faces", item, element_type="face")
            for item in raw_selections
        ]
        distance = _number(
            "move_planar_faces", "distance_mm", distance_mm
        )
        if abs(distance) <= 1.0e-12:
            raise _error(
                "move_planar_faces", "distance_mm", "must be non-zero", distance_mm
            )
        return self._graph(
            "model_move_planar_faces",
            "solid",
            clean_base,
            selections,
            distance,
            label=_label("move_planar_faces", label),
        )

    def hole(
        self,
        base: DomainValue,
        profile: DomainValue,
        diameter_mm: float,
        *,
        depth_mm: float | None = None,
        through_all: bool = False,
        countersink_diameter_mm: float | None = None,
        countersink_angle_degrees: float = 90.0,
        counterbore_diameter_mm: float | None = None,
        counterbore_depth_mm: float | None = None,
        reverse: bool = False,
        midplane: bool = False,
        direction: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Cut one diameter at every point or circle center in a sketch.

        Use direction=along_normal, opposite_normal, or symmetric. For an ordinary
        through hole, symmetric avoids dependence on which side holds the sketch.
        A counterbore or countersink is one-sided: put the sketch on its entry face
        and choose along_normal or opposite_normal into the material.
        """

        if through_all == (depth_mm is not None):
            raise _error(
                "hole",
                "depth_mm/through_all",
                "must provide exactly one depth or through_all=True",
            )
        if countersink_diameter_mm is not None and counterbore_diameter_mm is not None:
            raise _error(
                "hole",
                "countersink/counterbore",
                "cannot both be enabled on one hole feature",
            )
        if (counterbore_diameter_mm is None) != (counterbore_depth_mm is None):
            raise _error(
                "hole",
                "counterbore_diameter_mm/counterbore_depth_mm",
                "must be provided together",
            )
        diameter = _number(
            "hole", "diameter_mm", diameter_mm, minimum=0.0, strict=True
        )
        depth = (
            None
            if depth_mm is None
            else _number("hole", "depth_mm", depth_mm, minimum=0.0, strict=True)
        )
        countersink_diameter = (
            None
            if countersink_diameter_mm is None
            else _number(
                "hole",
                "countersink_diameter_mm",
                countersink_diameter_mm,
                minimum=0.0,
                strict=True,
            )
        )
        countersink_angle = _number(
            "hole",
            "countersink_angle_degrees",
            countersink_angle_degrees,
            minimum=0.0,
            strict=True,
        )
        counterbore_diameter = (
            None
            if counterbore_diameter_mm is None
            else _number(
                "hole",
                "counterbore_diameter_mm",
                counterbore_diameter_mm,
                minimum=0.0,
                strict=True,
            )
        )
        counterbore_depth = (
            None
            if counterbore_depth_mm is None
            else _number(
                "hole",
                "counterbore_depth_mm",
                counterbore_depth_mm,
                minimum=0.0,
                strict=True,
            )
        )
        if countersink_diameter is not None and countersink_diameter <= diameter:
            raise _error(
                "hole",
                "countersink_diameter_mm",
                "must be greater than diameter_mm",
                countersink_diameter,
            )
        if countersink_angle >= 180.0:
            raise _error(
                "hole",
                "countersink_angle_degrees",
                "must be less than 180",
                countersink_angle,
            )
        if counterbore_diameter is not None and counterbore_diameter <= diameter:
            raise _error(
                "hole",
                "counterbore_diameter_mm",
                "must be greater than diameter_mm",
                counterbore_diameter,
            )
        clean_reverse, clean_midplane, clean_direction = _linear_feature_direction(
            "hole",
            direction,
            reverse=reverse,
            midplane=midplane,
            subtractive=True,
        )
        shaped_cut = (
            "counterbore"
            if counterbore_diameter is not None
            else "countersink"
            if countersink_diameter is not None
            else ""
        )
        if shaped_cut and clean_midplane:
            raise _error(
                "hole",
                "direction",
                f"cannot be symmetric for a one-sided {shaped_cut}; place the "
                "sketch on its entry face and choose along_normal or "
                "opposite_normal into the material",
                clean_direction,
            )
        return self._graph(
            "hole",
            "feature",
            _feature("hole", "base", base),
            _hole_location_profile("hole", profile),
            diameter,
            depth_mm=depth,
            through_all=bool(through_all),
            countersink_diameter_mm=countersink_diameter,
            countersink_angle_degrees=countersink_angle,
            counterbore_diameter_mm=counterbore_diameter,
            counterbore_depth_mm=counterbore_depth,
            reverse=clean_reverse,
            midplane=clean_midplane,
            direction=clean_direction,
            label=_label("hole", label),
        )

    def holes(
        self,
        base: DomainValue,
        centers_mm: Sequence[Sequence[float]],
        diameter_mm: float,
        *,
        plane: str = "XY",
        plane_offset_mm: float = 0.0,
        placement: Mapping[str, Sequence[float]] | None = None,
        depth_mm: float | None = None,
        through_all: bool = False,
        countersink_diameter_mm: float | None = None,
        countersink_angle_degrees: float = 90.0,
        counterbore_diameter_mm: float | None = None,
        counterbore_depth_mm: float | None = None,
        direction: str = "symmetric",
        label: str = "",
    ) -> DomainValue:
        """Native Hole feature at one [u,v] center or many [[u,v],...] centers; a plain through bore may be symmetric, but a counterbore/countersink requires its plane on the entry face and a one-sided direction into material.

        Coordinates use the selected sketch plane. Omit depth_mm for a through
        hole; direction='symmetric' makes a plain bore independent of sketch-normal
        orientation. Provide depth_mm for a blind hole. An explicit through_all=True
        is also accepted. A counterbore or countersink is one-sided: set
        plane_offset_mm/placement on its entry face and explicitly choose
        along_normal or opposite_normal into the material.
        """

        clean_centers = _centers_mm("holes", centers_mm)
        points = [
            self.point(center, construction=True, name=f"HoleCenter{index + 1}")
            for index, center in enumerate(clean_centers)
        ]
        profile = self.sketch(
            points,
            plane=plane,
            plane_offset_mm=plane_offset_mm,
            placement=placement,
            require_closed_profile=False,
            label=f"{label} centers" if label else "Hole centers",
        )
        effective_through_all = bool(through_all or depth_mm is None)
        return self.hole(
            self._feature_base("holes", base, label=label),
            profile,
            diameter_mm,
            depth_mm=depth_mm,
            through_all=effective_through_all,
            countersink_diameter_mm=countersink_diameter_mm,
            countersink_angle_degrees=countersink_angle_degrees,
            counterbore_diameter_mm=counterbore_diameter_mm,
            counterbore_depth_mm=counterbore_depth_mm,
            direction=direction,
            label=label,
        )

    def bosses(
        self,
        base: DomainValue,
        centers_mm: Sequence[Sequence[float]],
        diameter_mm: float,
        height_mm: float,
        *,
        plane: str = "XY",
        plane_offset_mm: float = 0.0,
        placement: Mapping[str, Sequence[float]] | None = None,
        direction: str = "along_normal",
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Return one native additive feature for one [u,v] center or many [[u,v],...] centers."""

        clean_centers = _centers_mm("bosses", centers_mm)
        diameter = _number(
            "bosses",
            "diameter_mm",
            diameter_mm,
            minimum=0.0,
            strict=True,
        )
        circles = [
            self.circle(center, diameter / 2.0, name=f"Boss{index + 1}")
            for index, center in enumerate(clean_centers)
        ]
        profile = self.sketch(
            circles,
            plane=plane,
            plane_offset_mm=plane_offset_mm,
            placement=placement,
            require_closed_profile=True,
            label=f"{label} profiles" if label else "Boss profiles",
        )
        return self.extrude(
            profile,
            _number(
                "bosses",
                "height_mm",
                height_mm,
                minimum=0.0,
                strict=True,
            ),
            operation="add_material",
            base=self._feature_base("bosses", base, label=label),
            direction=direction,
            refine=refine,
            label=label,
        )

    def fastener_hole(
        self,
        base: DomainValue,
        profile: DomainValue,
        fastener: DomainValue,
        *,
        purpose: str = "clearance",
        fit: str = "normal",
        depth_mm: float | None = None,
        through_all: bool = False,
        reverse: bool = False,
        midplane: bool = False,
        direction: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Cut a catalog-sized hole with api.hole direction semantics."""

        if through_all == (depth_mm is not None):
            raise _error(
                "fastener_hole",
                "depth_mm/through_all",
                "must provide exactly one depth or through_all=True",
            )
        clean_purpose = str(purpose or "").strip().lower()
        if clean_purpose not in {
            "clearance",
            "tapped",
            "counterbore",
            "countersink",
        }:
            raise _error(
                "fastener_hole",
                "purpose",
                "must be clearance, tapped, counterbore, or countersink",
                purpose,
            )
        clean_fit = str(fit or "").strip().lower()
        if clean_fit not in {"normal", "close", "loose"}:
            raise _error(
                "fastener_hole",
                "fit",
                "must be normal, close, or loose",
                fit,
            )
        if clean_purpose == "tapped" and clean_fit != "normal":
            raise _error(
                "fastener_hole",
                "fit",
                "applies to clearance holes; tapped holes require normal",
                fit,
            )
        clean_fastener = _feature(
            "fastener_hole",
            "fastener",
            fastener,
        )
        if clean_fastener.operation != "fastener":
            raise _error(
                "fastener_hole",
                "fastener",
                "must be the exact value returned by api.fastener",
                clean_fastener.operation,
            )
        clean_reverse, clean_midplane, clean_direction = _linear_feature_direction(
            "fastener_hole",
            direction,
            reverse=reverse,
            midplane=midplane,
            subtractive=True,
        )
        return self._graph(
            "fastener_hole",
            "feature",
            _feature("fastener_hole", "base", base),
            _hole_location_profile("fastener_hole", profile),
            clean_fastener,
            purpose=clean_purpose,
            fit=clean_fit,
            depth_mm=(
                None
                if depth_mm is None
                else _number(
                    "fastener_hole",
                    "depth_mm",
                    depth_mm,
                    minimum=0.0,
                    strict=True,
                )
            ),
            through_all=bool(through_all),
            reverse=clean_reverse,
            midplane=clean_midplane,
            direction=clean_direction,
            label=_label("fastener_hole", label),
        )

    def draft(
        self,
        base: DomainValue,
        selection: Mapping[str, Any],
        angle_degrees: float,
        *,
        neutral_plane: str = "XY",
        pull_direction: str = "Z",
        reversed: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Taper selected feature faces about a neutral origin plane and pull axis."""

        clean_base = _feature("draft", "base", base)
        angle = _number(
            "draft", "angle_degrees", angle_degrees, minimum=0.0, strict=True
        )
        if angle >= 90.0:
            raise _error("draft", "angle_degrees", "must be less than 90", angle)
        return self._graph(
            "draft",
            "feature",
            clean_base,
            _selection("draft", selection, element_type="face"),
            angle,
            neutral_plane=_plane("draft", neutral_plane),
            pull_direction=_global_axis("draft", pull_direction),
            reversed=bool(reversed),
            label=_label("draft", label),
        )

    def subshape(
        self,
        shape: DomainValue,
        kind: str,
        selection: Mapping[str, Any],
        *,
        label: str = "",
    ) -> DomainValue:
        """Extract one subshape with a selector from api.find_subelements."""

        clean_shape = _topology("subshape", "shape", shape)
        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in {"edge", "wire", "face", "shell", "solid"}:
            raise _error(
                "subshape",
                "kind",
                "must be edge, wire, face, shell, or solid",
                kind,
            )
        # Unadvertised compatibility for unchanged saved programs.
        if isinstance(selection, int) and not isinstance(selection, bool):
            return self._direct(
                "subshape",
                clean_shape,
                clean_kind,
                _integer("subshape", "selection", selection, minimum=1),
                label=_label("subshape", label),
            )
        if clean_kind not in {"edge", "face"}:
            raise _error(
                "subshape",
                "selection",
                "can use a geometric query only for edge or face topology",
            )
        return self._graph(
            "model_subshape",
            clean_kind,
            clean_shape,
            _selection("subshape", selection, element_type=clean_kind),
            label=_label("subshape", label),
        )

    def defeature(
        self,
        shape: DomainValue,
        selection: Mapping[str, Any] | Sequence[int],
        *,
        label: str = "",
    ) -> DomainValue:
        """Remove selected feature faces and heal the solid parametrically."""

        clean_shape = _topology("defeature", "shape", shape, allowed={"solid"})
        if isinstance(selection, Mapping):
            return self._graph(
                "model_defeature",
                "solid",
                clean_shape,
                _selection("defeature", selection, element_type="face"),
                label=_label("defeature", label),
            )
        if not isinstance(selection, (list, tuple)) or not selection:
            raise _error(
                "defeature",
                "selection",
                "must be one geometric face query or positive 1-based face indexes",
            )
        return self._direct(
            "defeature",
            clean_shape,
            [
                _integer("defeature", f"selection[{index}]", item, minimum=1)
                for index, item in enumerate(selection)
            ],
            label=_label("defeature", label),
        )

    def find_subelements(
        self,
        *,
        element_type: str,
        expected_count: int,
        geometry_type: str = "",
        normal: Sequence[float] | None = None,
        direction: Sequence[float] | None = None,
        radius_mm: float | None = None,
        min_area_mm2: float | None = None,
        max_area_mm2: float | None = None,
        min_length_mm: float | None = None,
        max_length_mm: float | None = None,
        near_point: Sequence[float] | None = None,
        max_distance_mm: float | None = None,
        angle_tolerance_degrees: float = 1.0,
        radius_tolerance_mm: float = 1.0e-6,
    ) -> dict[str, Any]:
        """Build a stable face/edge selector with exact cardinality; near_point chooses the closest match while geometry, direction, radius, length, and area filters disambiguate it."""

        raw: dict[str, Any] = {
            "type": "query",
            "element_type": str(element_type or "").strip().lower(),
            "expected_count": expected_count,
        }
        if geometry_type:
            raw["geometry_type"] = geometry_type
        if normal is not None:
            raw["normal"] = normal
            raw["normal_tolerance_degrees"] = angle_tolerance_degrees
        if direction is not None:
            raw["direction"] = direction
            raw["direction_tolerance_degrees"] = angle_tolerance_degrees
        if radius_mm is not None:
            raw["radius"] = radius_mm
            raw["radius_tolerance"] = radius_tolerance_mm
        for target, value in (
            ("min_area", min_area_mm2),
            ("max_area", max_area_mm2),
            ("min_length", min_length_mm),
            ("max_length", max_length_mm),
            ("max_distance", max_distance_mm),
        ):
            if value is not None:
                raw[target] = value
        if near_point is not None:
            raw["near_point"] = near_point
        return _selection("find_subelements", raw)

    def measure(
        self,
        shape: DomainValue,
        quantity: str,
        *,
        other: DomainValue | None = None,
        selection: Mapping[str, Any] | None = None,
        other_selection: Mapping[str, Any] | None = None,
        material: DomainValue | None = None,
        expected: float | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        tolerance: float = 1.0e-6,
        label: str = "",
    ) -> DomainValue:
        """Verify a BREP quantity: length_mm, area_mm2, volume_mm3, solid/face/edge_count, bounds_(min|max|size)_(x|y|z)_mm, center_of_mass_(x|y|z)_mm, minimum_distance_mm, interference_volume_mm3, radius_mm, diameter_mm, mass_kg, inertia_(xx|xy|xz|yy|yz|zz)_kg_mm2, or minimum_wall_thickness_mm."""

        clean_quantity = str(quantity or "").strip().lower()
        if clean_quantity not in _MEASURE_QUANTITIES:
            raise _error(
                "measure",
                "quantity",
                f"must be one of {sorted(_MEASURE_QUANTITIES)}",
                quantity,
            )
        clean_other = (
            None
            if other is None
            else _modeled("measure", "other", other)
        )
        if (clean_quantity in _PAIR_MEASURE_QUANTITIES) != (clean_other is not None):
            raise _error(
                "measure",
                "other",
                (
                    f"is required for {clean_quantity}"
                    if clean_quantity in _PAIR_MEASURE_QUANTITIES
                    else f"does not apply to {clean_quantity}"
                ),
            )
        clean_selection = None
        clean_other_selection = None
        if clean_quantity in _RADIAL_MEASURE_QUANTITIES:
            clean_selection = _selection("measure", selection)
            if int(clean_selection["expected_count"]) != 1:
                raise _error(
                    "measure",
                    "selection.expected_count",
                    "must be 1 for a radius or diameter",
                )
        elif clean_quantity == "minimum_wall_thickness_mm":
            clean_selection = _selection(
                "measure",
                selection,
                element_type="face",
            )
            clean_other_selection = _selection(
                "measure",
                other_selection,
                element_type="face",
            )
            if (
                int(clean_selection["expected_count"]) != 1
                or int(clean_other_selection["expected_count"]) != 1
            ):
                raise _error(
                    "measure",
                    "selection.expected_count",
                    "must be 1 for both opposing wall faces",
                )
        elif selection is not None or other_selection is not None:
            raise _error(
                "measure",
                "selection/other_selection",
                f"does not apply to {clean_quantity}",
            )
        clean_material = _material_card(
            "measure",
            "material",
            material,
            optional=True,
        )
        if (clean_quantity in _MASS_MEASURE_QUANTITIES) != (
            clean_material is not None
        ):
            raise _error(
                "measure",
                "material",
                (
                    f"is required for {clean_quantity} and must provide Density"
                    if clean_quantity in _MASS_MEASURE_QUANTITIES
                    else f"does not apply to {clean_quantity}"
                ),
            )
        if clean_material is not None and "Density" not in set(
            clean_material.properties.get("require_physical_properties") or ()
        ):
            raise _error(
                "measure",
                "material",
                "must be created with require_physical_properties=['Density']",
            )
        if expected is None and minimum is None and maximum is None:
            raise _error(
                "measure", "expected/minimum/maximum", "must specify at least one bound"
            )
        clean_expected = None if expected is None else _number("measure", "expected", expected)
        clean_minimum = None if minimum is None else _number("measure", "minimum", minimum)
        clean_maximum = None if maximum is None else _number("measure", "maximum", maximum)
        if clean_minimum is not None and clean_maximum is not None and clean_minimum > clean_maximum:
            raise _error("measure", "minimum/maximum", "minimum must not exceed maximum")
        return self._graph(
            "measure",
            "check",
            _modeled("measure", "shape", shape),
            clean_quantity,
            other=clean_other,
            selection=clean_selection,
            other_selection=clean_other_selection,
            material=clean_material,
            expected=clean_expected,
            minimum=clean_minimum,
            maximum=clean_maximum,
            tolerance=_number("measure", "tolerance", tolerance, minimum=0.0),
            label=_label("measure", label),
        )

    def minimum_distance(
        self,
        first: DomainValue,
        second: DomainValue,
        *,
        expected: float | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        tolerance: float = 1.0e-6,
        label: str = "",
    ) -> DomainValue:
        """Verify exact BREP distance."""

        return self.measure(
            first,
            "minimum_distance_mm",
            other=second,
            expected=expected,
            minimum=minimum,
            maximum=maximum,
            tolerance=tolerance,
            label=label,
        )

    def material(
        self,
        material_uuid: str,
        *,
        require_physical_properties: Sequence[str] = (),
        require_appearance_properties: Sequence[str] = (),
    ) -> DomainValue:
        """Select one material catalog card by exact UUID for api.body or api.publish.

        Required property names make validation fail when the card lacks data consumed
        by the design.
        """

        return _retag(
            self._material.material(
                material_uuid,
                require_physical_properties=require_physical_properties,
                require_appearance_properties=require_appearance_properties,
            ),
            "partdesign",
        )

    def appearance(
        self,
        card: DomainValue | None = None,
        *,
        color_rgb: Sequence[int] | None = None,
        line_color_rgb: Sequence[int] | None = None,
        point_color_rgb: Sequence[int] | None = None,
        transparency_percent: int | None = None,
        line_width: float | None = None,
        point_size: float | None = None,
        display_mode: str | None = None,
        visible: bool | None = None,
        selectable: bool | None = None,
    ) -> DomainValue:
        """Define display properties for api.body or api.publish.

        RGB channels are integers from 0 through 255 and transparency is 0 through
        100 percent. Explicit values override card-derived appearance. This value
        styles a published result and is not itself an output.
        """

        clean_card = _material_card(
            "appearance",
            "card",
            card,
            optional=True,
        )
        material_card = (
            None if clean_card is None else _retag(clean_card, "material")
        )
        # Reuse the Material workbench's canonical display validation.  The
        # placeholder target is discarded; Part Design binds this style to its
        # stable publication object only after isolated geometry validation.
        canonical = self._material.appearance(
            {
                "document_uid": "partdesign-publication",
                "object_name": "Output",
            },
            material_card,
            shape_color=_rgb255("appearance", "color_rgb", color_rgb),
            line_color=_rgb255(
                "appearance",
                "line_color_rgb",
                line_color_rgb,
            ),
            point_color=_rgb255(
                "appearance",
                "point_color_rgb",
                point_color_rgb,
            ),
            transparency=transparency_percent,
            line_width=line_width,
            point_size=point_size,
            display_mode=display_mode,
            visibility=visible,
            selectable=selectable,
        )
        return DomainValue(
            domain="partdesign",
            operation="appearance",
            output_type="appearance",
            arguments=(clean_card,),
            properties=dict(canonical.properties),
        )

    def body(
        self,
        feature: DomainValue,
        *,
        interfaces: Mapping[str, Any] | None = None,
        checks: Sequence[DomainValue] = (),
        material: DomainValue | None = None,
        appearance: DomainValue | None = None,
        label: str = "",
    ) -> DomainValue:
        """Publish the final feature or one connected solid as a stable parametric Design Body; source edits retain Body identity and checks/material/appearance/interfaces attach here."""

        return self._graph(
            "body",
            "solid",
            _value(feature, {"feature", "solid"}, "feature", "body"),
            interfaces=_interfaces(interfaces),
            checks=self._measurement_checks("body", checks),
            material=_material_card(
                "body",
                "material",
                material,
                optional=True,
            ),
            appearance=_appearance(
                "body",
                "appearance",
                appearance,
                optional=True,
            ),
            label=_label("body", label),
        )

    def publish(
        self,
        shape: DomainValue,
        *,
        interfaces: Mapping[str, Any] | None = None,
        checks: Sequence[DomainValue] = (),
        material: DomainValue | None = None,
        appearance: DomainValue | None = None,
        label: str = "",
    ) -> DomainValue:
        """Publish standalone solid, shell, face, wire, or compound topology; use api.body instead for one solid with native Part Design history."""

        clean_shape = _topology("publish", "shape", shape, allowed=_PUBLISHABLE_TYPES)
        return self._graph(
            "publish",
            clean_shape.output_type,
            clean_shape,
            interfaces=_interfaces(interfaces),
            checks=self._measurement_checks("publish", checks),
            material=_material_card(
                "publish",
                "material",
                material,
                optional=True,
            ),
            appearance=_appearance(
                "publish",
                "appearance",
                appearance,
                optional=True,
            ),
            label=_label("publish", label),
        )

    @staticmethod
    def _measurement_checks(
        operation: str,
        checks: Sequence[DomainValue],
    ) -> list[DomainValue]:
        if not isinstance(checks, (list, tuple)) or len(checks) > 64:
            raise _error(operation, "checks", "must contain at most 64 api.measure values")
        return [
            _value(item, {"check"}, f"checks[{index}]", operation)
            for index, item in enumerate(checks)
        ]


def _direct_part_method(public_name: str, part_name: str):
    retained = getattr(PartDomainAPI, part_name)

    @wraps(retained)
    def call(self: PartDesignDomainAPI, *arguments: Any, **properties: Any) -> DomainValue:
        return self._direct(part_name, *arguments, **properties)

    call.__name__ = public_name
    call.__qualname__ = f"PartDesignDomainAPI.{public_name}"
    call.__doc__ = str(getattr(retained, "__doc__", "") or "").strip()
    return call


for _public_name, _part_name in _DIRECT_PART_EXPORTS:
    if not hasattr(PartDesignDomainAPI, _public_name):
        setattr(
            PartDesignDomainAPI,
            _public_name,
            _direct_part_method(_public_name, _part_name),
        )

del _public_name, _part_name


class _SavedPartDesignCompatibilityAPI(PartDesignDomainAPI):
    """Private replay adapter for unchanged programs authored against old names."""

    __slots__ = ("_enabled_compatibility_methods",)

    def __init__(
        self,
        exports: Iterable[str],
        output_types: Iterable[str],
        compatibility_methods: Iterable[str],
    ) -> None:
        enabled = frozenset(str(item) for item in compatibility_methods)
        unknown = enabled - _COMPATIBILITY_FEATURES
        if unknown:
            raise RuntimeError(
                "Unknown Part Design compatibility methods: "
                f"{sorted(unknown)!r}."
            )
        object.__setattr__(self, "_enabled_compatibility_methods", enabled)
        super().__init__(exports, output_types)

    def __getattribute__(self, name: str) -> Any:
        if name in _COMPATIBILITY_METHODS:
            enabled = object.__getattribute__(
                self,
                "_enabled_compatibility_methods",
            )
            if name not in enabled:
                raise AttributeError(
                    f"api.{name} is not enabled for this saved Part Design source."
                )
        return object.__getattribute__(self, name)

    def __dir__(self) -> list[str]:
        names = list(object.__dir__(self))
        enabled = object.__getattribute__(
            self,
            "_enabled_compatibility_methods",
        )
        return sorted(
            name
            for name in names
            if name not in _COMPATIBILITY_METHODS or name in enabled
        )

    def loft(
        self,
        sections: Sequence[DomainValue],
        *,
        base: DomainValue | None = None,
        operation: str | None = None,
        subtractive: bool | None = None,
        ruled: bool = False,
        closed: bool = False,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        enabled = object.__getattribute__(
            self,
            "_enabled_compatibility_methods",
        )
        explicit_intent = (
            None if operation is None else str(operation or "").strip().lower()
        )
        if explicit_intent is not None:
            explicit_intent = _operation_intent(
                "loft",
                explicit_intent,
                allow_creation=True,
            )
        if subtractive is not None:
            if "loft_subtractive" not in enabled:
                raise AttributeError(
                    "loft(subtractive=...) is not enabled for this saved "
                    "Part Design source."
                )
            legacy_intent = (
                "remove_material" if bool(subtractive) else "add_material"
            )
            if (
                explicit_intent is not None
                and explicit_intent != legacy_intent
            ):
                raise _error(
                    "loft",
                    "operation/subtractive",
                    "specify one consistent intent",
                )
            intent = legacy_intent
        else:
            intent = explicit_intent or "add_material"
        return super().loft(
            sections,
            base=base,
            operation=intent,
            ruled=ruled,
            closed=closed,
            refine=refine,
            label=label,
        )

    def pad(
        self,
        profile: DomainValue,
        length_mm: float,
        *,
        base: DomainValue | None = None,
        reverse: bool = False,
        midplane: bool = False,
        direction: str | None = None,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        return self._pad_feature(
            profile,
            length_mm,
            base=base,
            reverse=reverse,
            midplane=midplane,
            direction=direction,
            refine=refine,
            label=label,
            api_operation="pad",
        )

    def pocket(
        self,
        base: DomainValue,
        profile: DomainValue,
        length_mm: float | None = None,
        *,
        through_all: bool = False,
        reverse: bool = False,
        midplane: bool = False,
        direction: str | None = None,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        return self._pocket_feature(
            base,
            profile,
            length_mm,
            through_all=through_all,
            reverse=reverse,
            midplane=midplane,
            direction=direction,
            refine=refine,
            label=label,
            api_operation="pocket",
        )

    def groove(
        self,
        base: DomainValue,
        profile: DomainValue,
        angle_degrees: float = 360.0,
        *,
        axis: str = "V",
        reverse: bool = False,
        midplane: bool = False,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        return self.revolve(
            profile,
            angle_degrees,
            operation="remove_material",
            base=base,
            axis=_axis("groove", axis),
            reverse=bool(reverse),
            midplane=bool(midplane),
            refine=bool(refine),
            label=label,
        )


def create_partdesign_domain_api(
    exports: Iterable[str],
    output_types: Iterable[str],
    *,
    compatibility_methods: Iterable[str] = (),
) -> PartDesignDomainAPI:
    """Create the canonical API or the private unchanged-source replay adapter."""

    compatibility = tuple(
        dict.fromkeys(str(item) for item in compatibility_methods)
    )
    if compatibility:
        return _SavedPartDesignCompatibilityAPI(
            exports,
            output_types,
            compatibility,
        )
    return PartDesignDomainAPI(exports, output_types)
