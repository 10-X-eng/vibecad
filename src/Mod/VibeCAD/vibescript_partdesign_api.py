# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Part Design VibeScript programs.

The provider authors a declarative Body/sketch/feature graph.  The graph is
evaluated only in an isolated ``FreeCADCmd`` document; source never receives a
live document object or a GUI binding.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue
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


def _vector(operation: str, parameter: str, value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "must be [x, y, z]", value)
    return [_number(operation, f"{parameter}[{index}]", item) for index, item in enumerate(value)]


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
            result[key] = _vector(operation, f"selection.{key}", clean[key])
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
    """Body/sketch/feature graph API injected into Part Design source."""

    __slots__ = ("_next_feature_id", "_sketch_values", "_sketcher")

    domain = "partdesign"
    exported_names = (
        "point",
        "line",
        "arc",
        "circle",
        "ellipse",
        "bspline",
        "external_geometry",
        "constraint",
        "sketch",
        "pad",
        "pocket",
        "revolve",
        "groove",
        "loft",
        "polar_pattern",
        "mirror",
        "fillet",
        "chamfer",
        "body",
    )

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared = tuple(dict.fromkeys(str(item) for item in exports))
        if declared != self.exported_names:
            raise RuntimeError(
                "Part Design pack exports do not match the runtime contract: "
                f"expected {self.exported_names!r}, received {declared!r}."
            )
        if tuple(dict.fromkeys(str(item) for item in output_types)) != ("solid",):
            raise RuntimeError("Part Design must publish exactly the solid output type.")
        object.__setattr__(
            self,
            "_sketcher",
            SketcherDomainAPI(_SKETCH_EXPORTS, ("sketch",)),
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
        """Create an additive Pad, optionally on the current feature."""

        return self._graph(
            "pad",
            "feature",
            _profile("pad", "profile", profile),
            _number("pad", "length_mm", length_mm, minimum=0.0, strict=True),
            base=None if base is None else _feature("pad", "base", base),
            reverse=bool(reverse),
            midplane=bool(midplane),
            refine=bool(refine),
            label=_label("pad", label),
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
        """Create a subtractive Pocket from the current feature and profile."""

        if through_all == (length_mm is not None):
            raise _error(
                "pocket",
                "length_mm/through_all",
                "must provide exactly one of a positive length or through_all=True",
            )
        length = None if length_mm is None else _number(
            "pocket", "length_mm", length_mm, minimum=0.0, strict=True
        )
        return self._graph(
            "pocket",
            "feature",
            _feature("pocket", "base", base),
            _profile("pocket", "profile", profile),
            length,
            through_all=bool(through_all),
            reverse=bool(reverse),
            midplane=bool(midplane),
            refine=bool(refine),
            label=_label("pocket", label),
        )

    def revolve(
        self,
        profile: DomainValue,
        angle_degrees: float = 360.0,
        *,
        base: DomainValue | None = None,
        axis: str = "V",
        reverse: bool = False,
        midplane: bool = False,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Create an additive Revolution, optionally on the current feature."""

        angle = _number("revolve", "angle_degrees", angle_degrees, minimum=0.0, strict=True)
        if angle > 360.0:
            raise _error("revolve", "angle_degrees", "must not exceed 360", angle)
        return self._graph(
            "revolve",
            "feature",
            _profile("revolve", "profile", profile),
            angle,
            base=None if base is None else _feature("revolve", "base", base),
            axis=_axis("revolve", axis),
            reverse=bool(reverse),
            midplane=bool(midplane),
            refine=bool(refine),
            label=_label("revolve", label),
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
        """Create a subtractive Groove from the current feature and profile."""

        angle = _number("groove", "angle_degrees", angle_degrees, minimum=0.0, strict=True)
        if angle > 360.0:
            raise _error("groove", "angle_degrees", "must not exceed 360", angle)
        return self._graph(
            "groove",
            "feature",
            _feature("groove", "base", base),
            _profile("groove", "profile", profile),
            angle,
            axis=_axis("groove", axis),
            reverse=bool(reverse),
            midplane=bool(midplane),
            refine=bool(refine),
            label=_label("groove", label),
        )

    def loft(
        self,
        sections: Sequence[DomainValue],
        *,
        base: DomainValue | None = None,
        subtractive: bool = False,
        ruled: bool = False,
        closed: bool = False,
        refine: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Create an additive or subtractive Part Design loft."""

        if not isinstance(sections, (list, tuple)) or not 2 <= len(sections) <= 64:
            raise _error("loft", "sections", "must contain 2-64 profile values")
        clean_sections = [
            _profile("loft", f"sections[{index}]", item)
            for index, item in enumerate(sections)
        ]
        clean_base = None if base is None else _feature("loft", "base", base)
        if subtractive and clean_base is None:
            raise _error("loft", "base", "is required for a subtractive loft")
        return self._graph(
            "loft",
            "feature",
            clean_sections,
            base=clean_base,
            subtractive=bool(subtractive),
            ruled=bool(ruled),
            closed=bool(closed),
            refine=bool(refine),
            label=_label("loft", label),
        )

    def polar_pattern(
        self,
        base: DomainValue,
        occurrences: int,
        *,
        axis: str = "N",
        angle_degrees: float = 360.0,
        label: str = "",
    ) -> DomainValue:
        """Pattern the current feature around a profile or global axis."""

        angle = _number(
            "polar_pattern", "angle_degrees", angle_degrees, minimum=0.0, strict=True
        )
        if angle > 360.0:
            raise _error("polar_pattern", "angle_degrees", "must not exceed 360", angle)
        return self._graph(
            "polar_pattern",
            "feature",
            _feature("polar_pattern", "base", base),
            _integer("polar_pattern", "occurrences", occurrences, minimum=2),
            axis=_axis("polar_pattern", axis),
            angle_degrees=angle,
            label=_label("polar_pattern", label),
        )

    def mirror(
        self,
        base: DomainValue,
        plane: str,
        *,
        label: str = "",
    ) -> DomainValue:
        """Mirror the current feature across an origin plane."""

        return self._graph(
            "mirror",
            "feature",
            _feature("mirror", "base", base),
            plane=_plane("mirror", plane),
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

        return self._graph(
            "fillet",
            "feature",
            _feature("fillet", "base", base),
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

        return self._graph(
            "chamfer",
            "feature",
            _feature("chamfer", "base", base),
            _selection("chamfer", selection, element_type="edge", allow_all_edges=True),
            _number("chamfer", "size_mm", size_mm, minimum=0.0, strict=True),
            label=_label("chamfer", label),
        )

    def body(
        self,
        feature: DomainValue,
        *,
        interfaces: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Publish one exact single-solid Body result and semantic interfaces."""

        return self._graph(
            "body",
            "solid",
            _feature("body", "feature", feature),
            interfaces=_interfaces(interfaces),
            label=_label("body", label),
        )
