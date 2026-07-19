# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production provider-facing API for the Assembly VibeScript domain.

The API builds an immutable assembly graph.  Component source objects are
stable document references supplied through ``inputs``; the host snapshots
their geometry before the graph is evaluated in an isolated ``FreeCADCmd``
worker.  Distances are millimetres and angles are degrees.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
import math
import re
from typing import Any, Iterable

from vibescript_domain_api import DomainValue


_PUBLISHABLE_TYPES = frozenset(
    {
        "assembly",
        "component_link",
        "joint",
        "solver_diagnostics",
        "motion",
        "simulation",
        "exploded_view",
        "bom",
    }
)
_JOINT_TYPES = (
    "fixed",
    "revolute",
    "cylindrical",
    "slider",
    "ball",
    "distance",
    "parallel",
    "perpendicular",
    "angle",
    "rack_pinion",
    "screw",
    "gears",
    "belt",
)
_SUBELEMENT = re.compile(r"^(Face|Edge|Vertex)[1-9][0-9]*$")
_INTERFACE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MOTION_FUNCTIONS = frozenset({"abs", "asin", "arcsin", "arctan", "cos", "sin"})
_MOTION_NAMES = frozenset({"time", "initialValue", "pi"})
_OCCURRENCE_PATH = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*){0,15}$"
)
_BOM_PROPERTY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_BOM_BUILTINS = {
    "index": {
        "kind": "builtin",
        "key": "index",
        "heading": "Index",
        "native_name": "Index",
    },
    "name": {
        "kind": "builtin",
        "key": "name",
        "heading": "Name",
        "native_name": "Name",
    },
    "quantity": {
        "kind": "builtin",
        "key": "quantity",
        "heading": "Quantity",
        "native_name": "Quantity",
    },
    "file_name": {
        "kind": "builtin",
        "key": "file_name",
        "heading": "File Name",
        "native_name": "File Name",
    },
}


def _error(operation: str, parameter: str, message: str, value: Any = None) -> ValueError:
    received = "" if value is None else f" Received {value!r}."
    return ValueError(f"api.{operation}: invalid {parameter}: {message}.{received}")


def _number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool):
        raise _error(operation, parameter, "expected a finite number", value)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise _error(operation, parameter, "expected a finite number", value) from exc
    if not math.isfinite(result):
        raise _error(operation, parameter, "expected a finite number", value)
    if minimum is not None and (
        result <= minimum if strict_minimum else result < minimum
    ):
        relation = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    if maximum is not None and result > maximum:
        raise _error(operation, parameter, f"must not exceed {maximum:g}", value)
    return result


def _label(operation: str, value: Any) -> str:
    result = str(value or "").strip()
    if len(result) > 120:
        raise _error(operation, "label", "must contain at most 120 characters", value)
    return result


def _occurrence_path(operation: str, value: Any) -> str:
    result = str(value or "").strip()
    if not _OCCURRENCE_PATH.fullmatch(result):
        raise _error(
            operation,
            "occurrence_path",
            "must be one copy-ready source occurrence path with 1-16 '/'-separated "
            "FreeCAD object-name segments",
            value,
        )
    return result


def _bom_heading(operation: str, parameter: str, value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 80 or result.startswith("."):
        raise _error(
            operation,
            parameter,
            "must contain 1-80 characters and must not start with '.'",
            value,
        )
    return result


def _bom_columns(
    value: Sequence[str | Mapping[str, str]],
) -> tuple[list[dict[str, str]], set[str]]:
    operation = "bill_of_materials"
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(
            operation,
            "columns",
            "expected a sequence of built-in names or column objects",
            value,
        )
    if not 1 <= len(value) <= 32:
        raise _error(operation, "columns", "must contain 1-32 columns", value)
    columns: list[dict[str, str]] = []
    custom_headings: set[str] = set()
    seen_headings: set[str] = set()
    seen_native_names: set[str] = set()
    seen_builtins: set[str] = set()
    for index, raw in enumerate(value):
        parameter = f"columns[{index}]"
        if isinstance(raw, str):
            alias = raw.strip().lower().replace("-", "_").replace(" ", "_")
            column = _BOM_BUILTINS.get(alias)
            if column is None:
                raise _error(
                    operation,
                    parameter,
                    "string columns must be one of index, name, quantity, or file_name; "
                    "use {'property':'PartNumber','heading':'Part Number'} for a native "
                    "property or {'heading':'Description'} for custom row values",
                    raw,
                )
            if alias in seen_builtins:
                raise _error(operation, parameter, f"duplicates built-in column {alias!r}")
            seen_builtins.add(alias)
            clean = dict(column)
        elif isinstance(raw, Mapping):
            keys = set(raw)
            if keys in ({"property"}, {"property", "heading"}):
                property_name = str(raw.get("property") or "").strip()
                if not _BOM_PROPERTY.fullmatch(property_name):
                    raise _error(
                        operation,
                        f"{parameter}.property",
                        "must be one exact FreeCAD scalar property name",
                        raw.get("property"),
                    )
                heading = _bom_heading(
                    operation,
                    f"{parameter}.heading",
                    raw.get("heading") or property_name,
                )
                clean = {
                    "kind": "property",
                    "property": property_name,
                    "heading": heading,
                    "native_name": f".{property_name}",
                }
            elif keys == {"heading"}:
                heading = _bom_heading(
                    operation,
                    f"{parameter}.heading",
                    raw.get("heading"),
                )
                clean = {
                    "kind": "custom",
                    "heading": heading,
                    "native_name": heading,
                }
                custom_headings.add(heading)
            else:
                raise _error(
                    operation,
                    parameter,
                    "column objects must contain exactly property with optional heading, "
                    "or exactly heading for a custom column",
                    raw,
                )
        else:
            raise _error(
                operation,
                parameter,
                "expected a built-in string or column object",
                raw,
            )
        heading = str(clean["heading"])
        native_name = str(clean["native_name"])
        if heading in seen_headings or native_name in seen_native_names:
            duplicate = heading if heading in seen_headings else native_name
            raise _error(
                operation,
                parameter,
                f"duplicates column identity {duplicate!r}; keep one best version",
            )
        seen_headings.add(heading)
        seen_native_names.add(native_name)
        columns.append(clean)
    if "name" not in seen_builtins:
        raise _error(
            operation,
            "columns",
            "must include the 'name' built-in so native BOM rows and retained custom "
            "values have an unambiguous identity",
        )
    return columns, custom_headings


def _bom_overrides(
    value: Sequence[Mapping[str, Any]],
    *,
    custom_headings: set[str],
) -> list[dict[str, Any]]:
    operation = "bill_of_materials"
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(operation, "row_overrides", "expected a sequence of objects", value)
    if len(value) > 512:
        raise _error(operation, "row_overrides", "must contain at most 512 entries")
    result = []
    seen_paths: set[str] = set()
    for index, raw in enumerate(value):
        parameter = f"row_overrides[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != {"occurrence_path", "values"}:
            raise _error(
                operation,
                parameter,
                "must contain exactly occurrence_path and values",
                raw,
            )
        path = _occurrence_path(operation, raw.get("occurrence_path"))
        if path in seen_paths:
            raise _error(operation, f"{parameter}.occurrence_path", "is duplicated", path)
        seen_paths.add(path)
        values = raw.get("values")
        if not isinstance(values, Mapping) or not 1 <= len(values) <= 32:
            raise _error(
                operation,
                f"{parameter}.values",
                "must contain 1-32 custom heading/value pairs",
                values,
            )
        unknown = set(str(key) for key in values) - custom_headings
        if unknown:
            raise _error(
                operation,
                f"{parameter}.values",
                f"uses undeclared custom headings {sorted(unknown)}",
            )
        clean_values: dict[str, str | bool | int | float] = {}
        for heading, raw_value in values.items():
            if raw_value is None or isinstance(raw_value, (list, tuple, Mapping)):
                raise _error(
                    operation,
                    f"{parameter}.values[{heading!r}]",
                    "must be a JSON string, boolean, integer, or finite number",
                    raw_value,
                )
            if isinstance(raw_value, float) and not math.isfinite(raw_value):
                raise _error(
                    operation,
                    f"{parameter}.values[{heading!r}]",
                    "must be finite",
                    raw_value,
                )
            if isinstance(raw_value, str) and len(raw_value) > 4096:
                raise _error(
                    operation,
                    f"{parameter}.values[{heading!r}]",
                    "must contain at most 4096 characters",
                )
            if not isinstance(raw_value, (str, bool, int, float)):
                raise _error(
                    operation,
                    f"{parameter}.values[{heading!r}]",
                    "must be a JSON string, boolean, integer, or finite number",
                    raw_value,
                )
            clean_values[str(heading)] = raw_value
        result.append({"occurrence_path": path, "values": clean_values})
    return result


def _vector(
    operation: str,
    parameter: str,
    value: Any,
    *,
    size: int,
) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        description = "[x, y, z]" if size == 3 else "quaternion [x, y, z, w]"
        raise _error(operation, parameter, f"expected {description}", value)
    return [
        _number(operation, f"{parameter}[{index}]", item)
        for index, item in enumerate(value)
    ]


def _placement(operation: str, parameter: str, value: Any) -> dict[str, list[float]]:
    if value is None:
        position = [0.0, 0.0, 0.0]
        rotation = [0.0, 0.0, 0.0, 1.0]
    elif isinstance(value, (list, tuple)):
        position = _vector(operation, parameter, value, size=3)
        rotation = [0.0, 0.0, 0.0, 1.0]
    elif isinstance(value, Mapping):
        extra = set(value) - {"position", "rotation", "axis", "angle_degrees"}
        if extra:
            raise _error(
                operation,
                parameter,
                "supports position plus either rotation or axis/angle_degrees; "
                f"unknown keys {sorted(extra)}",
            )
        position = _vector(
            operation,
            f"{parameter}.position",
            value.get("position", [0.0, 0.0, 0.0]),
            size=3,
        )
        has_quaternion = "rotation" in value
        has_axis = "axis" in value
        has_angle = "angle_degrees" in value
        if has_quaternion and (has_axis or has_angle):
            raise _error(
                operation,
                parameter,
                "rotation cannot be combined with axis or angle_degrees",
                value,
            )
        if has_axis != has_angle:
            missing = "angle_degrees" if has_axis else "axis"
            raise _error(
                operation,
                parameter,
                f"axis and angle_degrees must be supplied together; missing {missing}",
                value,
            )
        if has_axis:
            axis = _vector(
                operation,
                f"{parameter}.axis",
                value["axis"],
                size=3,
            )
            axis_magnitude = math.sqrt(sum(item * item for item in axis))
            if axis_magnitude <= 1.0e-12:
                raise _error(
                    operation,
                    f"{parameter}.axis",
                    "axis-angle rotation requires a non-zero axis",
                    value["axis"],
                )
            half_angle = math.radians(
                _number(
                    operation,
                    f"{parameter}.angle_degrees",
                    value["angle_degrees"],
                )
            ) / 2.0
            scale = math.sin(half_angle) / axis_magnitude
            rotation = [
                axis[0] * scale,
                axis[1] * scale,
                axis[2] * scale,
                math.cos(half_angle),
            ]
        else:
            rotation = _vector(
                operation,
                f"{parameter}.rotation",
                value.get("rotation", [0.0, 0.0, 0.0, 1.0]),
                size=4,
            )
    else:
        raise _error(
            operation,
            parameter,
            "expected [x,y,z], a position/quaternion object, or a "
            "position/axis/angle_degrees object",
            value,
        )
    magnitude = math.sqrt(sum(item * item for item in rotation))
    if magnitude <= 1.0e-12:
        raise _error(operation, f"{parameter}.rotation", "quaternion must be non-zero")
    return {
        "position": position,
        "rotation": [item / magnitude for item in rotation],
    }


def _reference(operation: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"document_uid", "object_name"}:
        raise _error(
            operation,
            "source",
            "expected a stable input reference with document_uid and object_name",
            value,
        )
    result = {
        "document_uid": str(value.get("document_uid") or "").strip(),
        "object_name": str(value.get("object_name") or "").strip(),
    }
    if not result["document_uid"] or not result["object_name"]:
        raise _error(operation, "source", "document_uid and object_name must be non-empty")
    return result


def _domain_value(
    operation: str,
    parameter: str,
    value: Any,
    *,
    output_type: str,
) -> DomainValue:
    if not isinstance(value, DomainValue) or value.domain != "assembly":
        raise _error(
            operation,
            parameter,
            "expected a value returned by this Assembly api",
            type(value).__name__,
        )
    if value.output_type != output_type:
        raise _error(
            operation,
            parameter,
            f"expected an Assembly {output_type} value",
            value.output_type,
        )
    return value


def _values(
    operation: str,
    parameter: str,
    value: Any,
    *,
    output_type: str,
    minimum: int,
) -> list[DomainValue]:
    if isinstance(value, DomainValue):
        raw = [value]
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raise _error(operation, parameter, "expected an array of Assembly api values", value)
    if len(raw) < minimum:
        raise _error(operation, parameter, f"requires at least {minimum} value(s)", value)
    result = [
        _domain_value(
            operation,
            f"{parameter}[{index}]",
            item,
            output_type=output_type,
        )
        for index, item in enumerate(raw)
    ]
    if len({id(item) for item in result}) != len(result):
        raise _error(operation, parameter, "contains the same graph value more than once")
    return result


def _selection(operation: str, value: Any) -> dict[str, str]:
    if isinstance(value, str):
        clean = value.strip()
        if clean.lower() in {"", "origin", "component_origin"}:
            return {"type": "component_origin"}
        if _SUBELEMENT.fullmatch(clean):
            return {"type": "exact_subelement", "subelement": clean}
        raise _error(
            operation,
            "selection",
            "expected 'origin', FaceN, EdgeN, VertexN, or a published-interface object",
            value,
        )
    if not isinstance(value, Mapping):
        raise _error(operation, "selection", "expected a string or selection object", value)
    kind = str(value.get("type") or "").strip()
    if kind == "component_origin" and set(value) == {"type"}:
        return {"type": kind}
    if kind == "exact_subelement" and set(value) == {"type", "subelement"}:
        name = str(value.get("subelement") or "")
        if _SUBELEMENT.fullmatch(name):
            return {"type": kind, "subelement": name}
    if kind == "published_interface" and set(value) == {"type", "interface_name"}:
        name = str(value.get("interface_name") or "")
        if _INTERFACE_NAME.fullmatch(name):
            return {"type": kind, "interface_name": name}
    raise _error(
        operation,
        "selection",
        "selection must be exactly component_origin, exact_subelement, or "
        "published_interface with a valid name",
        value,
    )


def _anchor(operation: str, selection: Mapping[str, str], value: Any) -> str | None:
    if value is None:
        return None
    clean = str(value or "").strip()
    if not _SUBELEMENT.fullmatch(clean):
        raise _error(
            operation,
            "anchor",
            "expected an exact FaceN, EdgeN, or VertexN subelement",
            value,
        )
    selection_type = str(selection.get("type") or "")
    if selection_type != "exact_subelement":
        raise _error(
            operation,
            "anchor",
            "is supported only with an exact FaceN, EdgeN, or VertexN selection",
            value,
        )
    selected = str(selection.get("subelement") or "")
    if selected.startswith("Vertex") and clean != selected:
        raise _error(
            operation,
            "anchor",
            f"a vertex connector must use its selected vertex {selected}",
            value,
        )
    if clean != selected and not clean.startswith("Vertex"):
        raise _error(
            operation,
            "anchor",
            "use the selected subelement for its natural center or a VertexN "
            "belonging to the selected edge/face",
            value,
        )
    return clean


def _limits(
    operation: str,
    parameter: str,
    value: Any,
) -> list[float | None] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if set(value) - {"minimum", "maximum"}:
            raise _error(
                operation,
                parameter,
                "limit objects support only minimum and maximum",
                value,
            )
        raw = [value.get("minimum"), value.get("maximum")]
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        raw = list(value)
    else:
        raise _error(
            operation,
            parameter,
            "expected [minimum, maximum] or {'minimum': value, 'maximum': value}; "
            "either endpoint may be null for a one-sided limit",
            value,
        )
    if raw == [None, None]:
        raise _error(operation, parameter, "at least one limit endpoint is required", value)
    result = [
        None
        if item is None
        else _number(operation, f"{parameter}[{index}]", item)
        for index, item in enumerate(raw)
    ]
    if result[0] is not None and result[1] is not None and result[0] > result[1]:
        raise _error(operation, parameter, "minimum must not exceed maximum", value)
    return result


def _motion_formula(value: Any) -> str:
    operation = "motion"
    if not isinstance(value, str):
        raise _error(operation, "formula", "expected a native motion expression", value)
    formula = value.strip()
    if not formula:
        raise _error(operation, "formula", "must not be empty", value)
    if len(formula) > 512:
        raise _error(operation, "formula", "must contain at most 512 characters")
    if not formula.isascii():
        raise _error(operation, "formula", "must contain only ASCII expression syntax")
    try:
        tree = ast.parse(formula.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise _error(
            operation,
            "formula",
            f"invalid expression near column {exc.offset or 1}",
            value,
        ) from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 128:
        raise _error(operation, "formula", "expression is too complex")
    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.UAdd,
        ast.USub,
    )
    for node in nodes:
        if not isinstance(node, allowed_nodes):
            raise _error(
                operation,
                "formula",
                f"unsupported expression element {type(node).__name__}",
                value,
            )
        if isinstance(node, ast.Constant):
            if (
                isinstance(node.value, bool)
                or not isinstance(node.value, (int, float))
                or not math.isfinite(float(node.value))
            ):
                raise _error(
                    operation,
                    "formula",
                    "constants must be finite numbers",
                    node.value,
                )
        elif isinstance(node, ast.Name) and node.id not in (
            _MOTION_NAMES | _MOTION_FUNCTIONS
        ):
            raise _error(
                operation,
                "formula",
                f"unknown name {node.id!r}; use time, initialValue, pi, or a "
                f"supported function {sorted(_MOTION_FUNCTIONS)}",
            )
        elif isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.func.id not in _MOTION_FUNCTIONS
                or len(node.args) != 1
                or node.keywords
            ):
                raise _error(
                    operation,
                    "formula",
                    "functions must be one-argument calls to abs, asin/arcsin, "
                    "arctan, cos, or sin",
                    value,
                )
    return formula.replace("**", "^")


class AssemblyDomainAPI:
    """Explicit immutable graph API injected into Assembly VibeScript source."""

    __slots__ = ()

    domain = "assembly"
    exported_names = (
        "assembly",
        "component",
        "connector",
        "joint",
        "solve",
        "motion",
        "simulation",
        "exploded_view",
        "bill_of_materials",
    )

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared = tuple(dict.fromkeys(str(item) for item in exports))
        if declared != self.exported_names:
            raise RuntimeError(
                "Assembly pack exports do not match the production runtime contract: "
                f"expected {self.exported_names!r}, received {declared!r}."
            )
        if frozenset(str(item) for item in output_types) != _PUBLISHABLE_TYPES:
            raise RuntimeError(
                "Assembly pack output types do not match the production runtime contract."
            )

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        label: str = "",
        **properties: Any,
    ) -> DomainValue:
        clean_label = _label(operation, label)
        if clean_label:
            properties["label"] = clean_label
        return DomainValue(
            domain="assembly",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def component(
        self,
        source: Mapping[str, str],
        *,
        placement: Sequence[float] | Mapping[str, Sequence[float]] | None = None,
        grounded: bool = False,
        flexible: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Create one linked occurrence from a stable input reference.

        ``placement`` is either ``[x,y,z]`` or an object with ``position`` and
        either quaternion ``rotation=[x,y,z,w]`` or ``axis=[x,y,z]`` plus
        ``angle_degrees``. Set ``grounded=True`` for a fixed base occurrence.
        Set ``flexible=True`` only for an authenticated native Assembly source;
        its internal joints and stable occurrence paths then participate in the
        parent solve. A flexible occurrence cannot be grounded.
        Reuse the returned variable in connectors and return it exactly once as
        a ``component_link`` output.
        """

        operation = "component"
        if not isinstance(grounded, bool):
            raise _error(operation, "grounded", "expected a boolean", grounded)
        if not isinstance(flexible, bool):
            raise _error(operation, "flexible", "expected a boolean", flexible)
        if grounded and flexible:
            raise _error(
                operation,
                "grounded",
                "a native flexible subassembly cannot be grounded; ground a rigid base "
                "component in the parent assembly instead",
            )
        return self._value(
            operation,
            "component_link",
            _reference(operation, source),
            placement=_placement(operation, "placement", placement),
            grounded=grounded,
            flexible=flexible,
            label=label,
        )

    def connector(
        self,
        component: DomainValue,
        selection: str | Mapping[str, str] = "origin",
        *,
        occurrence_path: str | None = None,
        anchor: str | None = None,
        offset: Sequence[float] | Mapping[str, Sequence[float]] | None = None,
    ) -> DomainValue:
        """Select one component origin or exact/semantic subelement as a JCS.

        ``occurrence_path`` optionally targets one copy-ready internal source
        occurrence path exposed in Assembly domain context. It is required when
        a joint targets the internals of a flexible subassembly and works with
        the same stable path when that subassembly is rigid. ``anchor``
        optionally chooses an exact VertexN on the selected native
        edge/face; omit it to use the edge midpoint/circle center or face
        center. ``offset`` is an optional local placement applied after FreeCAD
        derives the connector frame. Use a published semantic interface for a
        regenerating Part Design publication; exact topology and anchors are
        accepted only for immutable native input snapshots.
        """

        operation = "connector"
        value = _domain_value(
            operation,
            "component",
            component,
            output_type="component_link",
        )
        clean_selection = _selection(operation, selection)
        return self._value(
            operation,
            "connector",
            value,
            selection=clean_selection,
            occurrence_path=(
                _occurrence_path(operation, occurrence_path)
                if occurrence_path is not None
                else None
            ),
            anchor=_anchor(operation, clean_selection, anchor),
            offset=_placement(operation, "offset", offset),
        )

    def joint(
        self,
        kind: str,
        first: DomainValue,
        second: DomainValue,
        *,
        distance_mm: float | None = None,
        angle_degrees: float | None = None,
        pitch_radius_mm: float | None = None,
        thread_pitch_mm: float | None = None,
        radius1_mm: float | None = None,
        radius2_mm: float | None = None,
        length_limits_mm: Sequence[float | None] | Mapping[str, float | None] | None = None,
        angle_limits_degrees: Sequence[float | None] | Mapping[str, float | None] | None = None,
        suppressed: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Connect two JCS values with one of FreeCAD's 13 native joint types.

        Type-specific values are required only for ``distance``, ``angle``,
        ``rack_pinion``, ``screw``, ``gears``, and ``belt``. Translation limits
        apply to slider/cylindrical joints; angular limits apply to
        revolute/cylindrical joints. Either limit endpoint may be ``None``.
        Rack/pinion pitch radius and screw pitch are signed and non-zero; their
        sign chooses motion direction. Gear and belt radii are positive.
        """

        operation = "joint"
        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in _JOINT_TYPES:
            raise _error(
                operation,
                "kind",
                f"must be one of {list(_JOINT_TYPES)}",
                kind,
            )
        first_value = _domain_value(
            operation,
            "first",
            first,
            output_type="connector",
        )
        second_value = _domain_value(
            operation,
            "second",
            second,
            output_type="connector",
        )
        if first_value.arguments[0] is second_value.arguments[0]:
            raise _error(
                operation,
                "first/second",
                "connectors must belong to two different component values",
            )
        if not isinstance(suppressed, bool):
            raise _error(operation, "suppressed", "expected a boolean", suppressed)

        supplied = {
            "distance_mm": distance_mm,
            "angle_degrees": angle_degrees,
            "pitch_radius_mm": pitch_radius_mm,
            "thread_pitch_mm": thread_pitch_mm,
            "radius1_mm": radius1_mm,
            "radius2_mm": radius2_mm,
        }
        required_by_kind = {
            "distance": ("distance_mm",),
            "angle": ("angle_degrees",),
            "rack_pinion": ("pitch_radius_mm",),
            "screw": ("thread_pitch_mm",),
            "gears": ("radius1_mm", "radius2_mm"),
            "belt": ("radius1_mm", "radius2_mm"),
        }
        required = set(required_by_kind.get(clean_kind, ()))
        missing = [name for name in required if supplied[name] is None]
        if missing:
            raise _error(
                operation,
                missing[0],
                f"is required for a {clean_kind} joint",
            )
        irrelevant = [
            name for name, value in supplied.items() if value is not None and name not in required
        ]
        if irrelevant:
            raise _error(
                operation,
                irrelevant[0],
                f"does not apply to a {clean_kind} joint",
                supplied[irrelevant[0]],
            )
        parameters: dict[str, float] = {}
        for name in required:
            if name in {"radius1_mm", "radius2_mm"}:
                parameters[name] = _number(
                    operation,
                    name,
                    supplied[name],
                    minimum=0.0,
                    strict_minimum=True,
                )
            else:
                parameters[name] = _number(operation, name, supplied[name])
                if name in {"pitch_radius_mm", "thread_pitch_mm"} and abs(
                    parameters[name]
                ) <= 1.0e-12:
                    raise _error(
                        operation,
                        name,
                        "must be non-zero; use the sign to select motion direction",
                        supplied[name],
                    )

        length_limits = _limits(operation, "length_limits_mm", length_limits_mm)
        angle_limits = _limits(
            operation,
            "angle_limits_degrees",
            angle_limits_degrees,
        )
        if length_limits is not None and clean_kind not in {"slider", "cylindrical"}:
            raise _error(
                operation,
                "length_limits_mm",
                "is supported only by slider and cylindrical joints",
                length_limits_mm,
            )
        if angle_limits is not None and clean_kind not in {"revolute", "cylindrical"}:
            raise _error(
                operation,
                "angle_limits_degrees",
                "is supported only by revolute and cylindrical joints",
                angle_limits_degrees,
            )
        return self._value(
            operation,
            "joint",
            first_value,
            second_value,
            kind=clean_kind,
            parameters=parameters,
            length_limits_mm=length_limits,
            angle_limits_degrees=angle_limits,
            suppressed=suppressed,
            label=label,
        )

    def assembly(
        self,
        components: Sequence[DomainValue],
        joints: Sequence[DomainValue] = (),
        *,
        label: str = "",
    ) -> DomainValue:
        """Build one assembly graph from returned component and joint variables.

        Every listed component and joint must also be returned exactly once as
        its own declared output.  At least one component must be grounded before
        the graph is solved.
        """

        operation = "assembly"
        component_values = _values(
            operation,
            "components",
            components,
            output_type="component_link",
            minimum=1,
        )
        joint_values = _values(
            operation,
            "joints",
            joints,
            output_type="joint",
            minimum=0,
        )
        component_ids = {id(item) for item in component_values}
        for index, joint_value in enumerate(joint_values):
            for connector_index, connector in enumerate(joint_value.arguments):
                component = connector.arguments[0]
                if id(component) not in component_ids:
                    raise _error(
                        operation,
                        f"joints[{index}].connector[{connector_index}]",
                        "references a component that is not listed in components",
                    )
        return self._value(
            operation,
            "assembly",
            components=component_values,
            joints=joint_values,
            label=label,
        )

    def solve(
        self,
        assembly: DomainValue,
        *,
        require_solved: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Solve the assembly in the worker and return structured native diagnostics.

        ``require_solved=True`` rejects and retains a candidate when FreeCAD
        reports conflicts, redundancy, malformed constraints, or no grounded
        component.  Set it false only when intentionally publishing a diagnostic
        snapshot of a non-solved graph.
        """

        operation = "solve"
        value = _domain_value(
            operation,
            "assembly",
            assembly,
            output_type="assembly",
        )
        if not isinstance(require_solved, bool):
            raise _error(operation, "require_solved", "expected a boolean", require_solved)
        return self._value(
            operation,
            "solver_diagnostics",
            value,
            require_solved=require_solved,
            label=label,
        )

    def motion(
        self,
        joint: DomainValue,
        formula: str,
        *,
        motion_type: str = "auto",
        label: str = "",
    ) -> DomainValue:
        """Drive one native revolute, slider, or cylindrical joint over time.

        Angular formulas produce radians and linear formulas millimetres. Use
        ``time`` in seconds; ``initialValue`` has radians for angular motion and
        millimetres for linear motion. Use ``pi``, arithmetic, powers with ``^``
        or ``**``, and the documented one-argument functions. ``auto``
        selects angular for revolute and linear for slider; cylindrical motion
        requires an explicit ``angular`` or ``linear`` choice.
        """

        operation = "motion"
        value = _domain_value(operation, "joint", joint, output_type="joint")
        joint_type = str(value.properties.get("kind") or "")
        if joint_type not in {"revolute", "slider", "cylindrical"}:
            raise _error(
                operation,
                "joint",
                "motion is supported only for revolute, slider, and cylindrical joints",
                joint_type,
            )
        if bool(value.properties.get("suppressed")):
            raise _error(operation, "joint", "cannot drive a suppressed joint")
        clean_type = str(motion_type or "").strip().lower()
        if clean_type == "auto":
            if joint_type == "cylindrical":
                raise _error(
                    operation,
                    "motion_type",
                    "cylindrical joints require explicit 'angular' or 'linear'",
                    motion_type,
                )
            clean_type = "angular" if joint_type == "revolute" else "linear"
        allowed = {
            "revolute": {"angular"},
            "slider": {"linear"},
            "cylindrical": {"angular", "linear"},
        }[joint_type]
        if clean_type not in allowed:
            raise _error(
                operation,
                "motion_type",
                f"must be one of {sorted(allowed)} for a {joint_type} joint",
                motion_type,
            )
        return self._value(
            operation,
            "motion",
            value,
            formula=_motion_formula(formula),
            motion_type=clean_type,
            label=label,
        )

    def simulation(
        self,
        assembly: DomainValue,
        motions: Sequence[DomainValue],
        *,
        start_time_s: float = 0.0,
        end_time_s: float = 1.0,
        time_step_s: float = 0.01,
        error_tolerance: float = 1.0e-6,
        frames_per_second: int = 30,
        label: str = "",
    ) -> DomainValue:
        """Run native Assembly kinematics in the worker and retain its trace.

        Every motion must also be returned as a stable ``motion`` output. The
        worker records an initial frame plus native time-series frames and
        rejects simulations exceeding 100000 component-pose samples.
        ``time_step_s`` controls trace density; ``frames_per_second`` is retained
        only as the live playback rate and does not add solver samples.
        """

        operation = "simulation"
        model = _domain_value(operation, "assembly", assembly, output_type="assembly")
        motion_values = _values(
            operation,
            "motions",
            motions,
            output_type="motion",
            minimum=1,
        )
        graph_joints = {id(item) for item in model.properties.get("joints", ())}
        seen_drives: set[tuple[int, str]] = set()
        for index, motion_value in enumerate(motion_values):
            joint = motion_value.arguments[0]
            if id(joint) not in graph_joints:
                raise _error(
                    operation,
                    f"motions[{index}]",
                    "drives a joint not listed in this assembly",
                )
            drive = (id(joint), str(motion_value.properties.get("motion_type") or ""))
            if drive in seen_drives:
                raise _error(
                    operation,
                    "motions",
                    "contains duplicate motion types for one joint",
                )
            seen_drives.add(drive)
        start = _number(operation, "start_time_s", start_time_s)
        end = _number(operation, "end_time_s", end_time_s)
        if end <= start:
            raise _error(
                operation,
                "end_time_s",
                "must be greater than start_time_s",
                end_time_s,
            )
        step = _number(
            operation,
            "time_step_s",
            time_step_s,
            minimum=0.0,
            strict_minimum=True,
        )
        tolerance = _number(
            operation,
            "error_tolerance",
            error_tolerance,
            minimum=1.0e-12,
            maximum=1.0,
        )
        if isinstance(frames_per_second, bool) or not isinstance(frames_per_second, int):
            raise _error(
                operation,
                "frames_per_second",
                "expected an integer from 1 through 240",
                frames_per_second,
            )
        if not 1 <= frames_per_second <= 240:
            raise _error(
                operation,
                "frames_per_second",
                "must be from 1 through 240",
                frames_per_second,
            )
        # OndselSolver retains the input state in addition to the requested
        # output-time states.  The extra slot also covers a non-integral final
        # interval without relying on a hidden solver rounding rule.
        estimated_frames = math.ceil((end - start) / step) + 2
        component_count = len(model.properties.get("components", ()))
        if estimated_frames > 10_000 or estimated_frames * component_count > 100_000:
            raise _error(
                operation,
                "time range/time_step_s",
                "would exceed 10000 native frames or 100000 component-pose samples; "
                "increase time_step_s or shorten the time range",
            )
        return self._value(
            operation,
            "simulation",
            model,
            motions=motion_values,
            start_time_s=start,
            end_time_s=end,
            time_step_s=step,
            error_tolerance=tolerance,
            frames_per_second=frames_per_second,
            estimated_frame_limit=estimated_frames,
            label=label,
        )

    def exploded_view(
        self,
        assembly: DomainValue,
        moves: Sequence[Mapping[str, Any]],
        *,
        label: str = "",
    ) -> DomainValue:
        """Create one native exploded view from ordered component moves.

        Each move contains ``components`` plus exactly one of ``transform`` or
        ``radial_distance_mm``. A normal ``transform`` uses the same placement
        form as ``api.component`` and is applied in order. A radial move uses
        FreeCAD's native radial control distance: displacement equals the vector
        from assembly-centre to component-centre, scaled by four times that
        distance divided by the assembly diagonal. Components may appear in
        later moves for staged explosions. The worker validates native final
        placements and explosion-line endpoints without changing solved state.
        """

        operation = "exploded_view"
        model = _domain_value(operation, "assembly", assembly, output_type="assembly")
        if not isinstance(moves, (list, tuple)) or not 1 <= len(moves) <= 64:
            raise _error(
                operation,
                "moves",
                "expected an array containing 1 through 64 ordered move objects",
                moves,
            )
        graph_components = {
            id(component): component
            for component in model.properties.get("components", ())
        }
        normalized_moves: list[dict[str, Any]] = []
        reference_count = 0
        for index, raw in enumerate(moves):
            path = f"moves[{index}]"
            if not isinstance(raw, Mapping):
                raise _error(operation, path, "expected an object", raw)
            extra = set(raw) - {"components", "transform", "radial_distance_mm"}
            if extra:
                raise _error(operation, path, f"unknown keys {sorted(extra)}", raw)
            has_transform = "transform" in raw
            has_radial = "radial_distance_mm" in raw
            if has_transform == has_radial:
                raise _error(
                    operation,
                    path,
                    "requires exactly one of transform or radial_distance_mm",
                    raw,
                )
            components = _values(
                operation,
                f"{path}.components",
                raw.get("components"),
                output_type="component_link",
                minimum=1,
            )
            for component_index, component in enumerate(components):
                if id(component) not in graph_components:
                    raise _error(
                        operation,
                        f"{path}.components[{component_index}]",
                        "is not listed in this assembly",
                    )
            reference_count += len(components)
            if reference_count > 256:
                raise _error(
                    operation,
                    "moves",
                    "may contain at most 256 component references across all moves",
                )
            if has_transform:
                transform = _placement(operation, f"{path}.transform", raw["transform"])
                translation_magnitude = math.sqrt(
                    sum(value * value for value in transform["position"])
                )
                rotation_change = math.sqrt(
                    sum(value * value for value in transform["rotation"][:3])
                )
                if translation_magnitude <= 1.0e-12 and rotation_change <= 1.0e-12:
                    raise _error(
                        operation,
                        f"{path}.transform",
                        "must translate or rotate at least one component",
                        raw["transform"],
                    )
                normalized_moves.append(
                    {
                        "kind": "normal",
                        "components": components,
                        "transform": transform,
                    }
                )
            else:
                distance = _number(
                    operation,
                    f"{path}.radial_distance_mm",
                    raw["radial_distance_mm"],
                    minimum=0.0,
                    maximum=1.0e6,
                    strict_minimum=True,
                )
                normalized_moves.append(
                    {
                        "kind": "radial",
                        "components": components,
                        "radial_distance_mm": distance,
                    }
                )
        return self._value(
            operation,
            "exploded_view",
            model,
            moves=normalized_moves,
            label=label,
        )

    def bill_of_materials(
        self,
        assembly: DomainValue,
        *,
        columns: Sequence[str | Mapping[str, str]] = ("index", "name", "quantity"),
        detail_subassemblies: bool = True,
        detail_parts: bool = True,
        only_parts: bool = False,
        row_overrides: Sequence[Mapping[str, Any]] = (),
        label: str = "",
    ) -> DomainValue:
        """Create one native, stable, authenticated Assembly bill of materials.

        Built-in columns are ``index``, ``name``, ``quantity``, and ``file_name``.
        A property column is ``{'property':'PartNumber','heading':'Part Number'}``;
        a custom column is ``{'heading':'Description'}``. Custom values are set
        with copy-ready occurrence paths in ``row_overrides``. The Quantity
        column aggregates repeated identical sources among siblings; every
        aggregated row retains all contributing occurrence paths for inspection.
        """

        operation = "bill_of_materials"
        value = _domain_value(operation, "assembly", assembly, output_type="assembly")
        for parameter, raw in (
            ("detail_subassemblies", detail_subassemblies),
            ("detail_parts", detail_parts),
            ("only_parts", only_parts),
        ):
            if not isinstance(raw, bool):
                raise _error(operation, parameter, "expected a boolean", raw)
        clean_columns, custom_headings = _bom_columns(columns)
        return self._value(
            operation,
            "bom",
            value,
            columns=clean_columns,
            detail_subassemblies=detail_subassemblies,
            detail_parts=detail_parts,
            only_parts=only_parts,
            row_overrides=_bom_overrides(
                row_overrides,
                custom_headings=custom_headings,
            ),
            label=label,
        )
