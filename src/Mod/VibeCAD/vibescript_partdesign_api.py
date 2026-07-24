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
_SKETCH_EXPORTS = SketcherDomainAPI.exported_names
_PUBLISHABLE_TYPES = ("solid", "shell", "face", "wire", "compound")
_TOPOLOGY_TYPES = frozenset({"edge", *_PUBLISHABLE_TYPES})
_MATERIAL_OPERATIONS = frozenset({"add_material", "remove_material"})
_CREATION_OPERATIONS = frozenset({"new_solid", "new_surface"})
_COMPATIBILITY_METHODS = frozenset({"pad", "pocket", "groove"})
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
    if mode != "query" or not set(clean) <= _QUERY_FIELDS:
        raise _error(
            operation,
            "selection",
            "must be a geometric query; transient FaceN/EdgeN names are forbidden",
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
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping) or len(value) > 64:
        raise _error("body", "interfaces", "must map at most 64 names to contracts", value)
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw in value.items():
        name = str(raw_name or "").strip()
        if not _NAME.fullmatch(name):
            raise _error("body", f"interfaces[{raw_name!r}]", "has an invalid stable name")
        if not isinstance(raw, Mapping) or not set(raw) <= {"selection", "description"}:
            raise _error(
                "body",
                f"interfaces[{name}]",
                "must contain selection and optional description",
                raw,
            )
        selection = raw.get("selection")
        if isinstance(selection, Mapping) and selection.get("type") == "origin":
            if set(selection) != {"type"}:
                raise _error("body", f"interfaces[{name}].selection", "origin accepts only type")
            clean_selection = {"type": "origin"}
        else:
            clean_selection = _selection("body", selection)
        description = str(raw.get("description") or "").strip()
        if len(description) > 500:
            raise _error("body", f"interfaces[{name}].description", "is too long")
        result[name] = {
            "selection": clean_selection,
            **({"description": description} if description else {}),
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
        "hole",
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
        if tuple(dict.fromkeys(str(item) for item in output_types)) != _PUBLISHABLE_TYPES:
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

    def _direct(self, part_operation: str, *arguments: Any, **properties: Any) -> DomainValue:
        """Call the retained OCC graph library and retag it for this domain."""

        method = getattr(self._part, part_operation)
        value = method(
            *tuple(_retag(item, "part") for item in arguments),
            **{key: _retag(item, "part") for key, item in properties.items()},
        )
        return _retag(value, "partdesign")

    def point(self, position: Sequence[float], *, construction: bool = True, name: str = "") -> DomainValue:
        """Create a construction point for a Part Design profile sketch."""

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
        """Create a finite profile line with addressable start/end points."""

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
        """Create a circular profile arc through three points."""

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
        """Create a full profile circle."""

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
        """Create a full elliptical profile curve."""

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
        """Create an interpolated or exact rational B-spline profile curve."""

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
        """Project one authenticated stable edge/vertex into a profile sketch."""

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
        driving: bool = True,
        active: bool = True,
        virtual: bool = False,
    ) -> DomainValue:
        """Create one named native Sketcher constraint for a profile."""

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
                driving=driving,
                active=active,
                virtual=virtual,
            ),
        )

    def sketch(
        self,
        geometry: Sequence[DomainValue],
        constraints: Sequence[DomainValue] = (),
        *,
        plane: str = "XY",
        z_offset_mm: float = 0.0,
        require_fully_constrained: bool = False,
        require_closed_profile: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Create a solver-validated Body profile on an origin plane."""

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
                "z_offset_mm": _number("sketch", "z_offset_mm", z_offset_mm),
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
        refine: bool,
        label: str,
        api_operation: str,
    ) -> DomainValue:
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
            reverse=bool(reverse),
            midplane=bool(midplane),
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
        refine: bool = True,
        vector: Sequence[float] | None = None,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Extrude with explicit new-solid, new-surface, add, or remove intent."""

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
        return self._graph(
            "pocket",
            "feature",
            _feature(api_operation, "base", base),
            _profile(api_operation, "profile", profile),
            length,
            through_all=bool(through_all),
            reverse=bool(reverse),
            midplane=bool(midplane),
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
        """Revolve with explicit new-solid, new-surface, add, or remove intent."""

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
        operation: str | None = None,
        subtractive: bool | None = None,
        ruled: bool = False,
        closed: bool = False,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Loft profiles to add material or remove material from a Body.

        Planar sections should be ``api.sketch`` values so publication preserves
        native sketches and a native loft feature.  Direct wires are for genuinely
        nonplanar or standalone topology.

        ``subtractive`` remains accepted only for saved VibeScript v2 programs;
        new source should state ``operation`` explicitly.
        """

        if not isinstance(sections, (list, tuple)) or not 2 <= len(sections) <= 64:
            raise _error("loft", "sections", "must contain 2-64 profile values")
        clean_sections = list(sections)
        explicit_intent = (
            None if operation is None else str(operation or "").strip().lower()
        )
        if explicit_intent is not None:
            explicit_intent = _operation_intent(
                "loft", explicit_intent, allow_creation=True
            )
        if subtractive is not None:
            legacy_intent = "remove_material" if bool(subtractive) else "add_material"
            if explicit_intent is not None and explicit_intent != legacy_intent:
                raise _error("loft", "operation/subtractive", "specify one consistent intent")
            intent = legacy_intent
        else:
            intent = explicit_intent or "add_material"
        if intent in _CREATION_OPERATIONS:
            if subtractive is not None:
                raise _error(
                    "loft",
                    "subtractive",
                    "is compatibility-only for Body material operations",
                )
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
        """Sweep ordered profiles along a path with explicit material intent."""

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
        """Sweep a closed profile on a helix to add or remove Body material."""

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
        """Union, subtract, or intersect arbitrary modeled geometry."""

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

    def compound(
        self,
        shapes: Sequence[DomainValue],
        *,
        label: str = "",
    ) -> DomainValue:
        """Group disconnected modeled shapes without pretending they form one solid."""

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
        """Pattern a Body feature or standalone shape around an explicit axis."""

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
        """Pattern modeled geometry along a global direction."""

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
        """Apply an ordered sequence of translate, rotate, mirror, or scale steps."""

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
        """Mirror a Body feature or standalone shape across one explicit plane."""

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
        """Round geometrically selected edges on the current feature."""

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
        """Bevel geometrically selected edges on the current feature."""

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
        """Hollow or thicken a solid after geometrically selecting removable faces."""

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
        label: str = "",
    ) -> DomainValue:
        """Create one native parametric hole from a point/circle location sketch."""

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
        return self._graph(
            "hole",
            "feature",
            _feature("hole", "base", base),
            _profile("hole", "profile", profile),
            diameter,
            depth_mm=depth,
            through_all=bool(through_all),
            countersink_diameter_mm=countersink_diameter,
            countersink_angle_degrees=countersink_angle,
            counterbore_diameter_mm=counterbore_diameter,
            counterbore_depth_mm=counterbore_depth,
            label=_label("hole", label),
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
        """Draft selected faces about an explicit neutral plane and pull direction."""

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
        selection: Mapping[str, Any] | int,
        *,
        label: str = "",
    ) -> DomainValue:
        """Extract one subshape by a stable geometric query.

        A positive 1-based index remains accepted for deterministic saved source,
        but new regenerating programs should pass ``api.find_subelements(...)``.
        """

        clean_shape = _topology("subshape", "shape", shape)
        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in {"edge", "wire", "face", "shell", "solid"}:
            raise _error(
                "subshape",
                "kind",
                "must be edge, wire, face, shell, or solid",
                kind,
            )
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
        """Build a count-guarded geometric selector; never rely on transient EdgeN/FaceN."""

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
        expected: float | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        tolerance: float = 1.0e-6,
        label: str = "",
    ) -> DomainValue:
        """Declare a deferred dimensional assertion evaluated on regenerated geometry."""

        clean_quantity = str(quantity or "").strip().lower()
        if clean_quantity not in {
            "length_mm",
            "area_mm2",
            "volume_mm3",
            "solid_count",
            "face_count",
            "edge_count",
        }:
            raise _error("measure", "quantity", "is not a supported shape measurement")
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
            expected=clean_expected,
            minimum=clean_minimum,
            maximum=clean_maximum,
            tolerance=_number("measure", "tolerance", tolerance, minimum=0.0),
            label=_label("measure", label),
        )

    def material(
        self,
        material_uuid: str,
        *,
        require_physical_properties: Sequence[str] = (),
        require_appearance_properties: Sequence[str] = (),
    ) -> DomainValue:
        """Select one exact Material-workbench catalog card for a published output."""

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
        """Define card-derived and/or explicit display state for one model output.

        RGB inputs use the same 8-bit channel values shown by FreeCAD's color
        editor.  The returned immutable value is passed to ``api.body`` or
        ``api.publish``; it is not itself a result output.
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
        """Publish one exact solid with optional material and appearance.

        Compatible sketch-based ``new_solid`` extrudes, revolves, and lofts
        become native initial Body features so their sketches and feature links
        remain editable after publication.  Passing a direct solid is a fallback
        for geometry that native sketch-and-feature history cannot represent.
        """

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
        """Publish exact standalone topology with optional material and appearance."""

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
    note = (
        " Retained for standalone, nonplanar, imported, or repair topology; prefer "
        "api.sketch and native Body features whenever they can represent the design."
    )
    call.__doc__ = str(getattr(retained, "__doc__", "") or "").strip() + note
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
        unknown = enabled - _COMPATIBILITY_METHODS
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

    def pad(
        self,
        profile: DomainValue,
        length_mm: float,
        *,
        base: DomainValue | None = None,
        reverse: bool = False,
        midplane: bool = False,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        return self._pad_feature(
            profile,
            length_mm,
            base=base,
            reverse=reverse,
            midplane=midplane,
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
