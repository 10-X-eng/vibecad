# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Sketcher VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_GEOMETRY_TYPES = frozenset(
    {
        "point",
        "line",
        "arc",
        "circle",
        "ellipse",
        "elliptic_arc",
        "hyperbolic_arc",
        "parabolic_arc",
        "bspline",
        "external_geometry",
    }
)
_CONSTRAINT_KINDS = frozenset(
    {
        "coincident",
        "horizontal",
        "vertical",
        "parallel",
        "perpendicular",
        "tangent",
        "distance",
        "distance_x",
        "distance_y",
        "angle",
        "angle_via_point",
        "radius",
        "diameter",
        "equal",
        "point_on_object",
        "symmetric",
        "block",
        "weight",
        "snells_law",
        "internal_alignment",
        "group",
        "text",
    }
)
_DIMENSIONAL_KINDS = frozenset(
    {
        "distance",
        "distance_x",
        "distance_y",
        "angle",
        "angle_via_point",
        "radius",
        "diameter",
        "weight",
        "snells_law",
    }
)
_INTERNAL_ALIGNMENTS = frozenset(
    {
        "ellipse_major_diameter",
        "ellipse_minor_diameter",
        "ellipse_focus1",
        "ellipse_focus2",
        "hyperbola_major_diameter",
        "hyperbola_minor_diameter",
        "hyperbola_focus",
        "parabola_focus",
        "parabola_focal_axis",
        "bspline_control_point",
        "bspline_knot_point",
    }
)
_POINTS_BY_GEOMETRY = {
    "point": frozenset({"none", "point"}),
    "line": frozenset({"none", "start", "end"}),
    "arc": frozenset({"none", "start", "end", "center"}),
    "circle": frozenset({"none", "center"}),
    "ellipse": frozenset({"none", "center"}),
    "elliptic_arc": frozenset({"none", "start", "end", "center"}),
    "hyperbolic_arc": frozenset({"none", "start", "end", "center"}),
    "parabolic_arc": frozenset({"none", "start", "end", "center"}),
    "bspline": frozenset({"none", "start", "end"}),
    "external_geometry": frozenset(
        {"none", "point", "start", "end", "center"}
    ),
}
_EXTERNAL_ENTITIES = frozenset({"x_axis", "y_axis", "origin"})
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MAX_GEOMETRY = 4096
_MAX_CONSTRAINTS = 16384


def _error(operation: str, parameter: str, reason: str, value: Any = None) -> ValueError:
    suffix = "" if value is None else f"; received {value!r}"
    return ValueError(f"api.{operation}: {parameter} {reason}{suffix}.")


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
    maximum: int,
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


def _point(operation: str, parameter: str, value: Any) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) not in {2, 3}:
        raise _error(operation, parameter, "must be [x,y] or planar [x,y,0]", value)
    result = tuple(
        _number(operation, f"{parameter}[{index}]", item)
        for index, item in enumerate(value)
    )
    if len(result) == 3 and abs(result[2]) > 1.0e-12:
        raise _error(operation, parameter, "must lie on the sketch plane (z=0)", value)
    return result[:2]


def _name(operation: str, parameter: str, value: Any) -> str:
    clean = str(value or "").strip()
    if clean and not _NAME.fullmatch(clean):
        raise _error(
            operation,
            parameter,
            "must start with a letter and contain at most 64 letters, digits, or underscores",
            value,
        )
    return clean


def _label(operation: str, value: Any) -> str:
    clean = str(value or "").strip()
    if len(clean) > 256:
        raise _error(operation, "label", "must contain at most 256 characters")
    return clean


def _geometry(operation: str, parameter: str, value: Any) -> DomainValue:
    if (
        not isinstance(value, DomainValue)
        or value.domain != "sketcher"
        or value.output_type != "sketch_geometry"
        or value.operation not in _GEOMETRY_TYPES
    ):
        raise _error(
            operation,
            parameter,
            "must be a geometry value returned by this Sketcher api",
            type(value).__name__,
        )
    return value


def _constraint(operation: str, parameter: str, value: Any) -> DomainValue:
    if (
        not isinstance(value, DomainValue)
        or value.domain != "sketcher"
        or value.output_type != "sketch_constraint"
        or value.operation != "constraint"
    ):
        raise _error(
            operation,
            parameter,
            "must be a constraint value returned by this Sketcher api",
            type(value).__name__,
        )
    return value


def _entity(operation: str, value: Any, *, index: int) -> dict[str, Any]:
    parameter = f"entities[{index}]"
    if isinstance(value, str):
        external = value.strip().lower()
        if external not in _EXTERNAL_ENTITIES:
            raise _error(
                operation,
                parameter,
                f"must be Sketcher geometry or one of {sorted(_EXTERNAL_ENTITIES)}",
                value,
            )
        return {"external": external, "point": "origin" if external == "origin" else "none"}
    if isinstance(value, DomainValue):
        geometry = _geometry(operation, parameter, value)
        return {"geometry": geometry, "point": "none"}
    if not isinstance(value, Mapping) or set(value) != {"geometry", "point"}:
        raise _error(
            operation,
            parameter,
            "must be geometry, an axis/origin string, or exactly {'geometry','point'}",
            value,
        )
    geometry = _geometry(operation, f"{parameter}.geometry", value.get("geometry"))
    point = str(value.get("point") or "none").strip().lower()
    allowed = _POINTS_BY_GEOMETRY[geometry.operation]
    if point not in allowed:
        raise _error(
            operation,
            f"{parameter}.point",
            f"must be one of {sorted(allowed)} for {geometry.operation} geometry",
            point,
        )
    return {"geometry": geometry, "point": point}


def _entity_has_point(entity: Mapping[str, Any]) -> bool:
    return str(entity.get("point") or "none") not in {"none", ""}


def _entity_geometry_operation(entity: Mapping[str, Any]) -> str:
    value = entity.get("geometry")
    return value.operation if isinstance(value, DomainValue) else ""


def _validate_constraint_shape(
    kind: str,
    entities: list[dict[str, Any]],
    value: float | None,
    *,
    alignment: str,
    internal_index: int,
    text: str,
    font: str,
) -> None:
    count = len(entities)
    pointed = [_entity_has_point(entity) for entity in entities]
    operations = [_entity_geometry_operation(entity) for entity in entities]

    def require_count(*counts: int) -> None:
        if count not in counts:
            expected = " or ".join(str(item) for item in counts)
            raise _error("constraint", "entities", f"requires {expected} entity value(s) for {kind}")

    if kind in {"horizontal", "vertical", "block"}:
        require_count(1)
        if pointed[0]:
            raise _error("constraint", "entities[0].point", f"must be 'none' for {kind}")
        if not operations[0]:
            raise _error("constraint", "entities[0]", f"must be sketch geometry for {kind}")
        if kind in {"horizontal", "vertical"} and operations[0] not in {
            "line",
            "external_geometry",
        }:
            raise _error("constraint", "entities[0]", f"must be line geometry for {kind}")
    elif kind in {"parallel", "perpendicular", "tangent", "equal"}:
        require_count(2)
        if not all(operations):
            raise _error(
                "constraint",
                "entities",
                f"{kind} requires two sketch geometry entities",
            )
        if kind in {"parallel", "equal"} and any(pointed):
            raise _error(
                "constraint",
                "entities",
                f"{kind} requires two whole geometry entities",
            )
        if kind in {"parallel", "perpendicular"} and any(
            operation not in {"line", "external_geometry"}
            for operation in operations
        ):
            raise _error(
                "constraint",
                "entities",
                f"{kind} requires line geometry",
            )
        if kind in {"perpendicular", "tangent"} and not pointed[0] and pointed[1]:
            raise _error(
                "constraint",
                "entities",
                f"{kind} point form must select a point on the first entity",
            )
    elif kind == "coincident":
        require_count(2)
        if not all(pointed):
            raise _error("constraint", "entities", "coincident requires two explicit points")
    elif kind == "point_on_object":
        require_count(2)
        if not pointed[0] or pointed[1]:
            raise _error(
                "constraint",
                "entities",
                "point_on_object requires [point entity, whole geometry/axis entity]",
            )
    elif kind in {"distance_x", "distance_y"}:
        require_count(1, 2)
        if not all(pointed):
            raise _error("constraint", "entities", f"{kind} requires one or two explicit points")
    elif kind == "distance":
        require_count(1, 2)
        if count == 2 and not all(pointed):
            raise _error("constraint", "entities", "two-entity distance requires two points")
        if count == 1 and pointed[0]:
            raise _error(
                "constraint",
                "entities[0].point",
                "single-entity distance measures whole geometry, so point must be 'none'",
            )
        if count == 1 and operations[0] not in {"line", "external_geometry"}:
            raise _error(
                "constraint",
                "entities[0]",
                "single-entity distance requires line geometry",
            )
    elif kind == "angle":
        require_count(1, 2)
        if count == 1 and pointed[0]:
            raise _error("constraint", "entities[0]", "single-geometry angle requires whole geometry")
        if count == 1 and operations[0] not in {
            "line",
            "arc",
            "external_geometry",
        }:
            raise _error(
                "constraint",
                "entities[0]",
                "single-geometry angle requires line or arc geometry",
            )
        if count == 2 and any(pointed) and not all(pointed):
            raise _error(
                "constraint",
                "entities",
                "point-based angle requires two explicit points",
            )
    elif kind == "angle_via_point":
        require_count(3)
        if not operations[0] or not operations[1] or pointed[0] or pointed[1]:
            raise _error(
                "constraint",
                "entities",
                "angle_via_point requires two whole curves followed by one explicit point",
            )
        if not pointed[2]:
            raise _error(
                "constraint",
                "entities[2]",
                "angle_via_point requires an explicit point selector",
            )
    elif kind in {"radius", "diameter", "weight"}:
        require_count(1)
        if pointed[0]:
            raise _error("constraint", "entities[0].point", f"must be 'none' for {kind}")
        if kind in {"radius", "diameter"} and operations[0] not in {
            "arc",
            "circle",
            "external_geometry",
        }:
            raise _error(
                "constraint",
                "entities[0]",
                f"must be arc or circle geometry for {kind}",
            )
        if kind == "weight" and (
            operations[0] != "circle"
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities[0]",
                "weight requires a construction circle used as a B-spline control handle",
            )
    elif kind == "symmetric":
        require_count(3)
        if not pointed[0] or not pointed[1]:
            raise _error("constraint", "entities", "symmetric requires two points and a symmetry point/axis")
    elif kind == "snells_law":
        require_count(3)
        if not pointed[0] or not pointed[1] or pointed[2]:
            raise _error(
                "constraint",
                "entities",
                "snells_law requires [first point, second point, interface geometry]",
            )
        if operations[2] not in {"line", "external_geometry"}:
            raise _error(
                "constraint",
                "entities[2]",
                "snells_law interface must be line geometry",
            )
    elif kind == "internal_alignment":
        if alignment not in _INTERNAL_ALIGNMENTS:
            raise _error(
                "constraint",
                "alignment",
                f"must be one of {sorted(_INTERNAL_ALIGNMENTS)}",
                alignment,
            )
        require_count(2)
        if alignment in {"ellipse_major_diameter", "ellipse_minor_diameter"} and (
            operations != ["line", "ellipse"]
            or any(pointed)
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities",
                f"{alignment} requires [whole construction line, whole ellipse]",
            )
        if alignment in {"ellipse_focus1", "ellipse_focus2"} and (
            operations != ["point", "ellipse"]
            or not pointed[0]
            or pointed[1]
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities",
                f"{alignment} requires [point entity, whole ellipse]",
            )
        if alignment in {"hyperbola_major_diameter", "hyperbola_minor_diameter"} and (
            operations != ["line", "hyperbolic_arc"]
            or any(pointed)
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities",
                f"{alignment} requires [whole construction line, whole hyperbolic arc]",
            )
        if alignment == "hyperbola_focus" and (
            operations != ["point", "hyperbolic_arc"]
            or not pointed[0]
            or pointed[1]
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities",
                "hyperbola_focus requires [construction point, whole hyperbolic arc]",
            )
        if alignment == "parabola_focus" and (
            operations != ["point", "parabolic_arc"]
            or not pointed[0]
            or pointed[1]
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities",
                "parabola_focus requires [construction point, whole parabolic arc]",
            )
        if alignment == "parabola_focal_axis" and (
            operations != ["line", "parabolic_arc"]
            or any(pointed)
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities",
                "parabola_focal_axis requires [whole construction line, whole parabolic arc]",
            )
        if alignment == "bspline_control_point" and (
            operations != ["circle", "bspline"]
            or str(entities[0].get("point") or "") != "center"
            or pointed[1]
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities",
                "bspline_control_point requires [construction-circle center, whole B-spline]",
            )
        if alignment == "bspline_control_point" and operations == ["circle", "bspline"]:
            spline = entities[1]["geometry"]
            pole_count = len(spline.arguments[0])
            if internal_index >= pole_count:
                raise _error(
                    "constraint",
                    "internal_index",
                    f"must select an existing B-spline control pole (0-{pole_count - 1})",
                    internal_index,
                )
        if alignment == "bspline_knot_point" and (
            operations != ["point", "bspline"]
            or str(entities[0].get("point") or "") != "point"
            or pointed[1]
            or not bool(entities[0]["geometry"].properties.get("construction"))
        ):
            raise _error(
                "constraint",
                "entities",
                "bspline_knot_point requires [construction point, whole B-spline]",
            )
        if alignment == "bspline_knot_point" and operations == ["point", "bspline"]:
            spline = entities[1]["geometry"]
            knots = list(spline.properties.get("knots") or [])
            if knots and internal_index >= len(knots):
                raise _error(
                    "constraint",
                    "internal_index",
                    f"must select an existing B-spline knot (0-{len(knots) - 1})",
                    internal_index,
                )
    elif kind in {"group", "text"}:
        if not entities:
            raise _error("constraint", "entities", f"requires at least one entity for {kind}")
        if operations[0] != "line" or pointed[0]:
            raise _error(
                "constraint",
                "entities[0]",
                f"must be the whole construction/group line for {kind}",
            )
        if kind == "text" and (not text or not font):
            raise _error("constraint", "text/font", "must both be non-empty for text")

    requires_value = kind in _DIMENSIONAL_KINDS
    if requires_value and value is None:
        raise _error("constraint", "value", f"is required for {kind}")
    if not requires_value and value is not None:
        raise _error("constraint", "value", f"does not apply to {kind}", value)


def _placement(operation: str, value: Any) -> dict[str, tuple[float, ...]]:
    if value is None:
        return {
            "position": (0.0, 0.0, 0.0),
            "rotation": (0.0, 0.0, 0.0, 1.0),
        }
    if not isinstance(value, Mapping) or set(value) != {"position", "rotation"}:
        raise _error(
            operation,
            "attachment_offset",
            "must contain exactly position and quaternion rotation",
            value,
        )
    position = value.get("position")
    rotation = value.get("rotation")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise _error(operation, "attachment_offset.position", "must be [x,y,z]", position)
    if not isinstance(rotation, (list, tuple)) or len(rotation) != 4:
        raise _error(
            operation,
            "attachment_offset.rotation",
            "must be quaternion [x,y,z,w]",
            rotation,
        )
    clean_position = tuple(
        _number(operation, f"attachment_offset.position[{index}]", item)
        for index, item in enumerate(position)
    )
    clean_rotation = tuple(
        _number(operation, f"attachment_offset.rotation[{index}]", item)
        for index, item in enumerate(rotation)
    )
    magnitude = math.sqrt(sum(item * item for item in clean_rotation))
    if magnitude <= 1.0e-12:
        raise _error(operation, "attachment_offset.rotation", "quaternion must be non-zero")
    return {
        "position": clean_position,
        "rotation": tuple(item / magnitude for item in clean_rotation),
    }


def _support(operation: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) not in (
        {"reference", "subelements"},
        {"reference", "selection"},
    ):
        raise _error(
            operation,
            "support",
            "must contain reference plus either subelements or selection",
            value,
        )
    reference = value.get("reference")
    if not isinstance(reference, Mapping) or set(reference) != {
        "document_uid",
        "object_name",
    }:
        raise _error(
            operation,
            "support.reference",
            "must contain exactly document_uid and object_name",
            reference,
        )
    clean_reference = {
        "document_uid": str(reference.get("document_uid") or "").strip(),
        "object_name": str(reference.get("object_name") or "").strip(),
    }
    if not clean_reference["document_uid"] or not clean_reference["object_name"]:
        raise _error(
            operation,
            "support.reference",
            "document_uid and object_name must be non-empty",
        )
    if "subelements" in value:
        selection: Any = {
            "type": "subelements",
            "subelements": value.get("subelements"),
        }
    else:
        selection = value.get("selection")
    if not isinstance(selection, Mapping):
        raise _error(operation, "support.selection", "must be an object", selection)
    selection_type = str(selection.get("type") or "").strip().lower()
    if selection_type == "subelements":
        if set(selection) != {"type", "subelements"}:
            raise _error(
                operation,
                "support.selection",
                "subelements selection must contain exactly type and subelements",
                selection,
            )
        subelements = selection.get("subelements")
        if (
            not isinstance(subelements, (list, tuple))
            or not 1 <= len(subelements) <= 4
        ):
            raise _error(
                operation,
                "support.selection.subelements",
                "must contain one to four names",
            )
        clean_subelements = []
        for index, item in enumerate(subelements):
            name = str(item or "").strip()
            if not re.fullmatch(r"(?:Face|Edge|Vertex)[1-9][0-9]*", name):
                raise _error(
                    operation,
                    f"support.selection.subelements[{index}]",
                    "must be FaceN, EdgeN, or VertexN",
                    item,
                )
            clean_subelements.append(name)
        if len(set(clean_subelements)) != len(clean_subelements):
            raise _error(
                operation,
                "support.selection.subelements",
                "must not contain duplicate names",
            )
        clean_selection = {
            "type": "subelements",
            "subelements": clean_subelements,
        }
    elif selection_type == "published_interface":
        if set(selection) != {"type", "interface_name"}:
            raise _error(
                operation,
                "support.selection",
                "published_interface selection must contain exactly type and interface_name",
                selection,
            )
        interface_name = str(selection.get("interface_name") or "").strip()
        if not _NAME.fullmatch(interface_name):
            raise _error(
                operation,
                "support.selection.interface_name",
                "must be a stable identifier",
                interface_name,
            )
        clean_selection = {
            "type": "published_interface",
            "interface_name": interface_name,
        }
    else:
        raise _error(
            operation,
            "support.selection.type",
            "must be 'subelements' or 'published_interface'",
            selection_type,
        )
    return {"reference": clean_reference, "selection": clean_selection}


class SketcherDomainAPI:
    """Explicit geometry/constraint graph API injected into Sketcher source."""

    __slots__ = ("_next_constraint_id", "_next_geometry_id")

    domain = "sketcher"
    exported_names = (
        "point",
        "line",
        "arc",
        "circle",
        "ellipse",
        "elliptic_arc",
        "hyperbolic_arc",
        "parabolic_arc",
        "bspline",
        "external_geometry",
        "constraint",
        "sketch",
    )

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared = tuple(dict.fromkeys(str(item) for item in exports))
        if declared != self.exported_names:
            raise RuntimeError(
                "Sketcher pack exports do not match the production runtime contract: "
                f"expected {self.exported_names!r}, received {declared!r}."
            )
        if tuple(dict.fromkeys(str(item) for item in output_types)) != ("sketch",):
            raise RuntimeError("Sketcher pack must publish exactly the sketch output type.")
        object.__setattr__(self, "_next_geometry_id", 1)
        object.__setattr__(self, "_next_constraint_id", 1)

    def _geometry_id(self) -> str:
        value = int(self._next_geometry_id)
        object.__setattr__(self, "_next_geometry_id", value + 1)
        return f"g{value}"

    def _constraint_id(self) -> str:
        value = int(self._next_constraint_id)
        object.__setattr__(self, "_next_constraint_id", value + 1)
        return f"c{value}"

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain="sketcher",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def point(
        self,
        position: Sequence[float],
        *,
        construction: bool = True,
        name: str = "",
    ) -> DomainValue:
        """Create one native sketch point with an explicit ``point`` selector."""

        if not isinstance(construction, bool):
            raise _error("point", "construction", "must be a boolean", construction)
        return self._value(
            "point",
            "sketch_geometry",
            _point("point", "position", position),
            construction=construction,
            name=_name("point", "name", name),
            graph_id=self._geometry_id(),
        )

    def line(
        self,
        start: Sequence[float],
        end: Sequence[float],
        *,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Create a finite line segment with addressable start and end points."""

        if not isinstance(construction, bool):
            raise _error("line", "construction", "must be a boolean", construction)
        clean_start = _point("line", "start", start)
        clean_end = _point("line", "end", end)
        if math.dist(clean_start, clean_end) <= 1.0e-12:
            raise _error("line", "end", "must differ from start", end)
        return self._value(
            "line",
            "sketch_geometry",
            clean_start,
            clean_end,
            construction=construction,
            name=_name("line", "name", name),
            graph_id=self._geometry_id(),
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
        """Create a circular arc through three non-collinear planar points."""

        if not isinstance(construction, bool):
            raise _error("arc", "construction", "must be a boolean", construction)
        points = [
            _point("arc", parameter, value)
            for parameter, value in (("start", start), ("through", through), ("end", end))
        ]
        cross = (points[1][0] - points[0][0]) * (points[2][1] - points[0][1]) - (
            points[1][1] - points[0][1]
        ) * (points[2][0] - points[0][0])
        if abs(cross) <= 1.0e-12:
            raise _error("arc", "start/through/end", "must be non-collinear")
        return self._value(
            "arc",
            "sketch_geometry",
            *points,
            construction=construction,
            name=_name("arc", "name", name),
            graph_id=self._geometry_id(),
        )

    def circle(
        self,
        center: Sequence[float],
        radius: float,
        *,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Create a full circle with an addressable center."""

        if not isinstance(construction, bool):
            raise _error("circle", "construction", "must be a boolean", construction)
        return self._value(
            "circle",
            "sketch_geometry",
            _point("circle", "center", center),
            _number("circle", "radius", radius, minimum=0.0, strict=True),
            construction=construction,
            name=_name("circle", "name", name),
            graph_id=self._geometry_id(),
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
        """Create a rotated full ellipse with major_radius >= minor_radius."""

        if not isinstance(construction, bool):
            raise _error("ellipse", "construction", "must be a boolean", construction)
        major = _number("ellipse", "major_radius", major_radius, minimum=0.0, strict=True)
        minor = _number("ellipse", "minor_radius", minor_radius, minimum=0.0, strict=True)
        if major < minor:
            raise _error("ellipse", "major_radius", "must be at least minor_radius", major_radius)
        return self._value(
            "ellipse",
            "sketch_geometry",
            _point("ellipse", "center", center),
            major,
            minor,
            rotation_degrees=_number("ellipse", "rotation_degrees", rotation_degrees),
            construction=construction,
            name=_name("ellipse", "name", name),
            graph_id=self._geometry_id(),
        )

    def elliptic_arc(
        self,
        center: Sequence[float],
        major_radius: float,
        minor_radius: float,
        start_parameter: float,
        end_parameter: float,
        *,
        rotation_degrees: float = 0.0,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Create a trimmed ellipse using OCC curve parameters in radians."""

        if not isinstance(construction, bool):
            raise _error("elliptic_arc", "construction", "must be a boolean", construction)
        major = _number(
            "elliptic_arc", "major_radius", major_radius, minimum=0.0, strict=True
        )
        minor = _number(
            "elliptic_arc", "minor_radius", minor_radius, minimum=0.0, strict=True
        )
        if major < minor:
            raise _error(
                "elliptic_arc",
                "major_radius",
                "must be at least minor_radius",
                major_radius,
            )
        start = _number("elliptic_arc", "start_parameter", start_parameter)
        end = _number("elliptic_arc", "end_parameter", end_parameter)
        span = end - start
        if abs(span) <= 1.0e-12 or abs(span) > 2.0 * math.pi + 1.0e-12:
            raise _error(
                "elliptic_arc",
                "start_parameter/end_parameter",
                "must define a non-zero span no greater than 2*pi radians",
            )
        return self._value(
            "elliptic_arc",
            "sketch_geometry",
            _point("elliptic_arc", "center", center),
            major,
            minor,
            start,
            end,
            rotation_degrees=_number(
                "elliptic_arc", "rotation_degrees", rotation_degrees
            ),
            construction=construction,
            name=_name("elliptic_arc", "name", name),
            graph_id=self._geometry_id(),
        )

    def hyperbolic_arc(
        self,
        center: Sequence[float],
        major_radius: float,
        minor_radius: float,
        start_parameter: float,
        end_parameter: float,
        *,
        rotation_degrees: float = 0.0,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Create a trimmed hyperbola using OCC's dimensionless curve parameter."""

        if not isinstance(construction, bool):
            raise _error("hyperbolic_arc", "construction", "must be a boolean", construction)
        major = _number(
            "hyperbolic_arc", "major_radius", major_radius, minimum=0.0, strict=True
        )
        minor = _number(
            "hyperbolic_arc", "minor_radius", minor_radius, minimum=0.0, strict=True
        )
        start = _number("hyperbolic_arc", "start_parameter", start_parameter)
        end = _number("hyperbolic_arc", "end_parameter", end_parameter)
        if abs(end - start) <= 1.0e-12:
            raise _error(
                "hyperbolic_arc",
                "start_parameter/end_parameter",
                "must define a non-zero parameter span",
            )
        if max(abs(start), abs(end)) > 20.0:
            raise _error(
                "hyperbolic_arc",
                "start_parameter/end_parameter",
                "must stay within [-20,20] to keep coordinates bounded",
            )
        return self._value(
            "hyperbolic_arc",
            "sketch_geometry",
            _point("hyperbolic_arc", "center", center),
            major,
            minor,
            start,
            end,
            rotation_degrees=_number(
                "hyperbolic_arc", "rotation_degrees", rotation_degrees
            ),
            construction=construction,
            name=_name("hyperbolic_arc", "name", name),
            graph_id=self._geometry_id(),
        )

    def parabolic_arc(
        self,
        vertex: Sequence[float],
        focal_length: float,
        start_parameter: float,
        end_parameter: float,
        *,
        rotation_degrees: float = 0.0,
        construction: bool = False,
        name: str = "",
    ) -> DomainValue:
        """Create a trimmed parabola from its vertex, focus distance, and parameters."""

        if not isinstance(construction, bool):
            raise _error("parabolic_arc", "construction", "must be a boolean", construction)
        focal = _number(
            "parabolic_arc", "focal_length", focal_length, minimum=0.0, strict=True
        )
        start = _number("parabolic_arc", "start_parameter", start_parameter)
        end = _number("parabolic_arc", "end_parameter", end_parameter)
        if abs(end - start) <= 1.0e-12:
            raise _error(
                "parabolic_arc",
                "start_parameter/end_parameter",
                "must define a non-zero parameter span",
            )
        if max(abs(start), abs(end)) > 1.0e6:
            raise _error(
                "parabolic_arc",
                "start_parameter/end_parameter",
                "must stay within [-1000000,1000000] millimetres",
            )
        return self._value(
            "parabolic_arc",
            "sketch_geometry",
            _point("parabolic_arc", "vertex", vertex),
            focal,
            start,
            end,
            rotation_degrees=_number(
                "parabolic_arc", "rotation_degrees", rotation_degrees
            ),
            construction=construction,
            name=_name("parabolic_arc", "name", name),
            graph_id=self._geometry_id(),
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
        """Create an interpolated or exact rational B-spline sketch geometry.

        Omit ``degree/knots/multiplicities/weights`` to interpolate through the
        points. Supplying ``degree`` switches to exact NURBS control-pole data.
        """

        if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= 512:
            raise _error("bspline", "points", "must contain 3-512 planar points", points)
        if not isinstance(periodic, bool) or not isinstance(construction, bool):
            raise _error("bspline", "periodic/construction", "must be booleans")
        clean_points = [
            _point("bspline", f"points[{index}]", point)
            for index, point in enumerate(points)
        ]
        exact = degree is not None
        if not exact and (knots or multiplicities or weights):
            raise _error(
                "bspline",
                "degree",
                "is required when knots, multiplicities, or weights are supplied",
            )
        clean_degree = None
        clean_knots: list[float] = []
        clean_multiplicities: list[int] = []
        clean_weights: list[float] = []
        if exact:
            clean_degree = _integer("bspline", "degree", degree, minimum=1, maximum=25)
            if clean_degree >= len(clean_points):
                raise _error("bspline", "degree", "must be smaller than the point count", degree)
            if not isinstance(knots, (list, tuple)) or len(knots) < 2:
                raise _error("bspline", "knots", "must contain at least two values", knots)
            clean_knots = [
                _number("bspline", f"knots[{index}]", item)
                for index, item in enumerate(knots)
            ]
            if any(right <= left for left, right in zip(clean_knots, clean_knots[1:])):
                raise _error("bspline", "knots", "must be strictly increasing", knots)
            if not isinstance(multiplicities, (list, tuple)) or len(multiplicities) != len(
                clean_knots
            ):
                raise _error(
                    "bspline",
                    "multiplicities",
                    f"must contain exactly {len(clean_knots)} values",
                    multiplicities,
                )
            clean_multiplicities = [
                _integer(
                    "bspline",
                    f"multiplicities[{index}]",
                    item,
                    minimum=1,
                    maximum=clean_degree + 1,
                )
                for index, item in enumerate(multiplicities)
            ]
            relation = (
                sum(clean_multiplicities[:-1])
                if periodic
                else sum(clean_multiplicities)
            )
            expected = len(clean_points) if periodic else len(clean_points) + clean_degree + 1
            if relation != expected:
                raise _error(
                    "bspline",
                    "multiplicities",
                    f"sum relation is {relation}, but this curve requires {expected}",
                    multiplicities,
                )
            if weights:
                if not isinstance(weights, (list, tuple)) or len(weights) != len(clean_points):
                    raise _error(
                        "bspline",
                        "weights",
                        f"must contain exactly {len(clean_points)} values",
                        weights,
                    )
                clean_weights = [
                    _number(
                        "bspline",
                        f"weights[{index}]",
                        item,
                        minimum=0.0,
                        strict=True,
                    )
                    for index, item in enumerate(weights)
                ]
        return self._value(
            "bspline",
            "sketch_geometry",
            clean_points,
            degree=clean_degree,
            knots=clean_knots,
            multiplicities=clean_multiplicities,
            weights=clean_weights,
            periodic=periodic,
            tolerance=_number("bspline", "tolerance", tolerance, minimum=0.0, strict=True),
            construction=construction,
            name=_name("bspline", "name", name),
            graph_id=self._geometry_id(),
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
        """Project one stable Edge/Vertex reference into the native sketch."""

        if not isinstance(defining, bool) or not isinstance(intersection, bool):
            raise _error(
                "external_geometry",
                "defining/intersection",
                "must be booleans",
            )
        clean_selection: Mapping[str, Any]
        if isinstance(selection, str):
            clean_selection = {
                "type": "subelements",
                "subelements": [selection],
            }
        elif isinstance(selection, Mapping):
            clean_selection = selection
        else:
            raise _error(
                "external_geometry",
                "selection",
                "must be EdgeN/VertexN or a selection object",
                selection,
            )
        resolved = _support(
            "external_geometry",
            {"reference": reference, "selection": clean_selection},
        )
        assert resolved is not None
        if resolved["selection"]["type"] == "subelements":
            subelements = resolved["selection"]["subelements"]
            if len(subelements) != 1 or not re.fullmatch(
                r"(?:Edge|Vertex)[1-9][0-9]*",
                subelements[0],
            ):
                raise _error(
                    "external_geometry",
                    "selection.subelements",
                    "must contain exactly one EdgeN or VertexN",
                    subelements,
                )
        return self._value(
            "external_geometry",
            "sketch_geometry",
            resolved["reference"],
            resolved["selection"],
            construction=True,
            defining=defining,
            intersection=intersection,
            name=_name("external_geometry", "name", name),
            graph_id=self._geometry_id(),
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
        """Create one named native constraint from geometry handles.

        Entity point selectors use ``{'geometry': line, 'point': 'start'}``.
        Whole geometry values omit that wrapper. The strings ``x_axis``,
        ``y_axis``, and ``origin`` address Sketcher's external axes/origin.
        Angles are degrees; all other dimensional values are millimetres or
        dimensionless weights/ratios as appropriate.
        """

        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in _CONSTRAINT_KINDS:
            raise _error(
                "constraint",
                "kind",
                f"must be one of {sorted(_CONSTRAINT_KINDS)}",
                kind,
            )
        if not isinstance(entities, (list, tuple)) or not entities:
            raise _error("constraint", "entities", "must be a non-empty array", entities)
        if len(entities) > 128:
            raise _error("constraint", "entities", "may contain at most 128 entries")
        clean_entities = [
            _entity("constraint", item, index=index)
            for index, item in enumerate(entities)
        ]
        clean_value = (
            None
            if value is None
            else _number("constraint", "value", value, minimum=0.0 if clean_kind in {"radius", "diameter", "weight", "snells_law"} else None, strict=clean_kind in {"radius", "diameter", "weight", "snells_law"})
        )
        clean_alignment = str(alignment or "").strip().lower()
        clean_internal_index = _integer(
            "constraint",
            "internal_index",
            internal_index,
            minimum=0,
            maximum=4095,
        )
        clean_text = str(text or "")
        clean_font = str(font or "").strip()
        if len(clean_text) > 4096 or len(clean_font) > 256:
            raise _error("constraint", "text/font", "exceeds the supported length")
        _validate_constraint_shape(
            clean_kind,
            clean_entities,
            clean_value,
            alignment=clean_alignment,
            internal_index=clean_internal_index,
            text=clean_text,
            font=clean_font,
        )
        if not all(isinstance(item, bool) for item in (driving, active, virtual, text_height)):
            raise _error(
                "constraint",
                "driving/active/virtual/text_height",
                "must be booleans",
            )
        clean_expression = str(expression or "").strip()
        if clean_expression and clean_kind not in _DIMENSIONAL_KINDS:
            raise _error(
                "constraint",
                "expression",
                f"requires a dimensional constraint, not {clean_kind}",
            )
        if len(clean_expression) > 4096:
            raise _error("constraint", "expression", "must contain at most 4096 characters")
        if not driving and clean_expression:
            raise _error(
                "constraint",
                "expression",
                "cannot drive a reference (driving=False) constraint",
            )
        if not driving and clean_kind not in _DIMENSIONAL_KINDS:
            raise _error(
                "constraint",
                "driving",
                f"can be false only for dimensional constraints, not {clean_kind}",
            )
        return self._value(
            "constraint",
            "sketch_constraint",
            clean_kind,
            clean_entities,
            value=clean_value,
            name=_name("constraint", "name", name),
            expression=clean_expression,
            driving=driving,
            active=active,
            virtual=virtual,
            alignment=clean_alignment,
            internal_index=clean_internal_index,
            text=clean_text,
            font=clean_font,
            text_height=text_height,
            graph_id=self._constraint_id(),
        )

    def sketch(
        self,
        geometry: Sequence[DomainValue],
        constraints: Sequence[DomainValue] = (),
        *,
        support: Mapping[str, Any] | None = None,
        map_mode: str = "Deactivated",
        attachment_offset: Mapping[str, Any] | None = None,
        require_fully_constrained: bool = False,
        require_closed_profile: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Build one stable native sketch from an immutable geometry graph.

        Set ``require_fully_constrained`` and/or ``require_closed_profile`` when
        downstream manufacturing or Part Design operations depend on those
        invariants; the worker rejects a candidate that does not meet them.
        """

        if not isinstance(geometry, (list, tuple)) or not 1 <= len(geometry) <= _MAX_GEOMETRY:
            raise _error(
                "sketch",
                "geometry",
                f"must contain 1-{_MAX_GEOMETRY} geometry values",
            )
        if not isinstance(constraints, (list, tuple)) or len(constraints) > _MAX_CONSTRAINTS:
            raise _error(
                "sketch",
                "constraints",
                f"must contain at most {_MAX_CONSTRAINTS} constraint values",
            )
        clean_geometry = [
            _geometry("sketch", f"geometry[{index}]", item)
            for index, item in enumerate(geometry)
        ]
        clean_constraints = [
            _constraint("sketch", f"constraints[{index}]", item)
            for index, item in enumerate(constraints)
        ]
        if len({id(item) for item in clean_geometry}) != len(clean_geometry):
            raise _error("sketch", "geometry", "contains the same graph value more than once")
        if len({id(item) for item in clean_constraints}) != len(clean_constraints):
            raise _error("sketch", "constraints", "contains the same graph value more than once")
        geometry_ids = {id(item) for item in clean_geometry}
        names = [str(item.properties.get("name") or "") for item in clean_geometry]
        named = [name for name in names if name]
        if len(named) != len(set(named)):
            raise _error("sketch", "geometry.name", "must be unique within the sketch")
        constraint_names = [
            str(item.properties.get("name") or "") for item in clean_constraints
        ]
        named_constraints = [name for name in constraint_names if name]
        if len(named_constraints) != len(set(named_constraints)):
            raise _error("sketch", "constraint.name", "must be unique within the sketch")
        for constraint_index, item in enumerate(clean_constraints):
            entities = list(item.arguments[1])
            for entity_index, entity in enumerate(entities):
                referenced = entity.get("geometry") if isinstance(entity, Mapping) else None
                if referenced is not None and id(referenced) not in geometry_ids:
                    raise _error(
                        "sketch",
                        f"constraints[{constraint_index}].entities[{entity_index}]",
                        "references geometry not listed in this sketch",
                    )
        if not isinstance(require_fully_constrained, bool) or not isinstance(
            require_closed_profile, bool
        ):
            raise _error(
                "sketch",
                "require_fully_constrained/require_closed_profile",
                "must be booleans",
            )
        clean_map_mode = str(map_mode or "").strip()
        if not clean_map_mode or len(clean_map_mode) > 128:
            raise _error("sketch", "map_mode", "must be a non-empty mode name", map_mode)
        return self._value(
            "sketch",
            "sketch",
            clean_geometry,
            clean_constraints,
            support=_support("sketch", support),
            map_mode=clean_map_mode,
            attachment_offset=_placement("sketch", attachment_offset),
            require_fully_constrained=require_fully_constrained,
            require_closed_profile=require_closed_profile,
            label=_label("sketch", label),
        )
