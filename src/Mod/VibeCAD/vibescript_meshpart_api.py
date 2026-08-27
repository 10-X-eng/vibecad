# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production MeshPart VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = ("mesh_from_shape", "shape_from_mesh")
_OUTPUT_TYPES = ("mesh", "solid", "shell", "face", "wire", "compound")
_MESH_METHODS = (
    "standard",
    "max_length",
    "max_area",
    "local_length",
    "deflection",
    "min_max_length",
    "netgen_fineness",
    "netgen_custom",
)
_NETGEN_FINENESS = {
    "very_coarse": 0,
    "coarse": 1,
    "moderate": 2,
    "fine": 3,
    "very_fine": 4,
}
_SURFACE_OUTPUT_TYPES = frozenset({"solid", "shell", "face", "compound"})
_BOUNDARY_OUTPUT_TYPES = frozenset({"wire", "compound"})
_SUBELEMENT = re.compile(r"^(Face|Shell|Solid)([1-9][0-9]*)$")
_MAX_LABEL_CHARS = 256
_MAX_SELECTION_ITEMS = 1_000_000
_MAX_SUBELEMENTS = 256
_MAX_LENGTH = 1_000_000_000.0
_MISSING = object()


class MeshPartAPIError(ValueError):
    """A source error carrying one exact repair target for the operating model."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        parameter: str,
        reason: str,
    ) -> None:
        self.details = {
            "stage": "source_validation",
            "operation": operation,
            "parameter": parameter,
            "reason": reason,
            "correction": (
                f"Correct api.{operation} parameter {parameter!r}: it {reason}. "
                "Change only the failing source expression, then retry against the "
                "failed working revision."
            ),
        }
        super().__init__(message)


def _error(
    operation: str,
    parameter: str,
    reason: str,
    value: Any = _MISSING,
) -> MeshPartAPIError:
    if value is _MISSING:
        suffix = ""
    elif isinstance(value, (list, tuple)) and len(value) > 12:
        suffix = f"; received {type(value).__name__} with {len(value)} items"
    elif isinstance(value, str) and len(value) > 256:
        suffix = f"; received string with {len(value)} characters"
    else:
        suffix = f"; received {value!r}"
    return MeshPartAPIError(
        f"api.{operation}: {parameter} {reason}{suffix}.",
        operation=operation,
        parameter=parameter,
        reason=reason,
    )


def _number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(operation, parameter, "must be a finite number", value)
    clean = float(value)
    if not math.isfinite(clean):
        raise _error(operation, parameter, "must be finite", value)
    if minimum is not None and (
        clean <= minimum if strict_minimum else clean < minimum
    ):
        relation = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    if maximum is not None and clean > maximum:
        raise _error(operation, parameter, f"must be at most {maximum:g}", value)
    return clean


def _optional_positive(
    operation: str,
    parameter: str,
    value: Any,
) -> float | None:
    if value is None:
        return None
    return _number(
        operation,
        parameter,
        value,
        minimum=0.0,
        maximum=_MAX_LENGTH,
        strict_minimum=True,
    )


def _boolean(operation: str, parameter: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise _error(operation, parameter, "must be true or false", value)
    return value


def _label(operation: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > _MAX_LABEL_CHARS or "\0" in value:
        received = (
            type(value).__name__
            if not isinstance(value, str)
            else f"string with {len(value)} characters"
        )
        raise _error(
            operation,
            "label",
            f"must be a string of at most {_MAX_LABEL_CHARS} characters without nulls",
            received,
        )
    return value


def _reference(operation: str, parameter: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _error(
            operation,
            parameter,
            "must be a stable input reference with exactly document_uid and object_name",
            type(value).__name__,
        )
    result = {}
    for name in ("document_uid", "object_name"):
        raw = value.get(name)
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            raise _error(
                operation,
                f"{parameter}.{name}",
                "must be a non-empty string without leading or trailing whitespace",
                raw,
            )
        if len(raw) > 256 or "\0" in raw:
            raise _error(
                operation,
                f"{parameter}.{name}",
                "must contain at most 256 characters without nulls",
                raw,
            )
        result[name] = raw
    return result


def _enum(
    operation: str,
    parameter: str,
    value: Any,
    choices: Iterable[str],
) -> str:
    if not isinstance(value, str):
        raise _error(operation, parameter, "must be a string enum", value)
    clean = value.strip().lower()
    allowed = tuple(choices)
    if clean not in allowed:
        raise _error(operation, parameter, f"must be one of {list(allowed)!r}", value)
    return clean


def _subelements(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise _error(
            "mesh_from_shape",
            "subelements",
            "must be null or an array such as ['Face1', 'Face2']",
            value,
        )
    if len(value) > _MAX_SUBELEMENTS:
        raise _error(
            "mesh_from_shape",
            "subelements",
            f"may contain at most {_MAX_SUBELEMENTS} items",
            value,
        )
    result = []
    selector_kind = ""
    for index, raw in enumerate(value):
        match = _SUBELEMENT.fullmatch(raw) if isinstance(raw, str) else None
        if match is None:
            raise _error(
                "mesh_from_shape",
                f"subelements[{index}]",
                "must be a 1-based FaceN, ShellN, or SolidN selector",
                raw,
            )
        if selector_kind and match.group(1) != selector_kind:
            raise _error(
                "mesh_from_shape",
                "subelements",
                "must select only one topology class (FaceN, ShellN, or SolidN)",
                value,
            )
        selector_kind = match.group(1)
        if raw in result:
            raise _error(
                "mesh_from_shape",
                "subelements",
                f"contains duplicate selector {raw!r}",
            )
        result.append(raw)
    result.sort(key=lambda selector: int(_SUBELEMENT.fullmatch(selector).group(2)))
    return tuple(result)


def _facet_indices(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not value:
        raise _error(
            "shape_from_mesh",
            "facet_indices",
            "must be null or a non-empty array of 1-based facet indices",
            value,
        )
    if len(value) > _MAX_SELECTION_ITEMS:
        raise _error(
            "shape_from_mesh",
            "facet_indices",
            f"may contain at most {_MAX_SELECTION_ITEMS} indices",
            value,
        )
    result = []
    seen = set()
    for index, raw in enumerate(value):
        if isinstance(raw, bool) or type(raw) is not int or raw < 1:
            raise _error(
                "shape_from_mesh",
                f"facet_indices[{index}]",
                "must be a positive 1-based integer",
                raw,
            )
        if raw in seen:
            raise _error(
                "shape_from_mesh",
                "facet_indices",
                f"contains duplicate index {raw}",
            )
        seen.add(raw)
        result.append(raw)
    return tuple(sorted(result))


def _reject_irrelevant(
    operation: str,
    method: str,
    values: Mapping[str, Any],
    allowed: frozenset[str],
) -> None:
    for name, value in values.items():
        if name not in allowed and value is not None:
            raise _error(
                operation,
                name,
                f"is not used by method={method!r}; remove it or choose its matching method",
                value,
            )


class MeshPartDomainAPI:
    """Immutable BREP/mesh conversion API injected into MeshPart source."""

    __slots__ = ("_domain",)

    exported_names = _EXPORTS

    def __init__(
        self,
        exports: Iterable[str],
        output_types: Iterable[str],
        *,
        domain: str = "meshpart",
    ) -> None:
        if tuple(dict.fromkeys(str(item) for item in exports)) != _EXPORTS:
            raise RuntimeError(f"MeshPart pack exports must be exactly {_EXPORTS!r}.")
        if tuple(dict.fromkeys(str(item) for item in output_types)) != _OUTPUT_TYPES:
            raise RuntimeError(
                f"MeshPart pack output types must be exactly {_OUTPUT_TYPES!r}."
            )
        clean_domain = str(domain or "").strip().lower()
        if clean_domain not in {"mesh", "meshpart"}:
            raise RuntimeError(
                "MeshPart conversion values may belong only to the Mesh or "
                "MeshPart VibeScript domains."
            )
        object.__setattr__(self, "_domain", clean_domain)

    @property
    def domain(self) -> str:
        return self._domain

    def mesh_from_shape(
        self,
        source: Mapping[str, str],
        *,
        subelements: Sequence[str] | None = None,
        method: str = "standard",
        linear_deflection: float | None = None,
        angular_deflection_degrees: float | None = None,
        relative: bool | None = None,
        preserve_face_groups: bool | None = None,
        max_length: float | None = None,
        max_area: float | None = None,
        local_length: float | None = None,
        deflection: float | None = None,
        min_length: float | None = None,
        fineness: str | None = None,
        growth_rate: float | None = None,
        segments_per_edge: float | None = None,
        segments_per_radius: float | None = None,
        second_order: bool | None = None,
        optimize: bool | None = None,
        allow_quad: bool | None = None,
        label: str = "",
    ) -> DomainValue:
        """Mesh one authenticated BREP with a selected native mesher overload."""

        operation = "mesh_from_shape"
        clean_method = _enum(operation, "method", method, _MESH_METHODS)
        raw_method_options = {
            "linear_deflection": linear_deflection,
            "angular_deflection_degrees": angular_deflection_degrees,
            "relative": relative,
            "preserve_face_groups": preserve_face_groups,
            "max_length": max_length,
            "max_area": max_area,
            "local_length": local_length,
            "deflection": deflection,
            "min_length": min_length,
            "fineness": fineness,
            "growth_rate": growth_rate,
            "segments_per_edge": segments_per_edge,
            "segments_per_radius": segments_per_radius,
            "second_order": second_order,
            "optimize": optimize,
            "allow_quad": allow_quad,
        }
        allowed_by_method = {
            "standard": frozenset(
                {
                    "linear_deflection",
                    "angular_deflection_degrees",
                    "relative",
                    "preserve_face_groups",
                }
            ),
            "max_length": frozenset({"max_length"}),
            "max_area": frozenset({"max_area"}),
            "local_length": frozenset({"local_length"}),
            "deflection": frozenset({"deflection"}),
            "min_max_length": frozenset({"min_length", "max_length"}),
            "netgen_fineness": frozenset(
                {
                    "fineness",
                    "min_length",
                    "max_length",
                    "second_order",
                    "optimize",
                    "allow_quad",
                }
            ),
            "netgen_custom": frozenset(
                {
                    "growth_rate",
                    "segments_per_edge",
                    "segments_per_radius",
                    "min_length",
                    "max_length",
                    "second_order",
                    "optimize",
                    "allow_quad",
                }
            ),
        }
        _reject_irrelevant(
            operation,
            clean_method,
            raw_method_options,
            allowed_by_method[clean_method],
        )
        clean_max_length = _optional_positive(operation, "max_length", max_length)
        clean_max_area = _optional_positive(operation, "max_area", max_area)
        clean_local_length = _optional_positive(operation, "local_length", local_length)
        clean_deflection = _optional_positive(operation, "deflection", deflection)
        clean_min_length = _optional_positive(operation, "min_length", min_length)
        method_values = {
            "max_length": clean_max_length,
            "max_area": clean_max_area,
            "local_length": clean_local_length,
            "deflection": clean_deflection,
        }
        if clean_method in method_values and method_values[clean_method] is None:
            raise _error(
                operation,
                clean_method,
                f"is required when method={clean_method!r}",
            )
        if clean_method == "min_max_length":
            if clean_min_length is None or clean_max_length is None:
                raise _error(
                    operation,
                    "min_length/max_length",
                    "are both required when method='min_max_length'",
                )
            if clean_min_length > clean_max_length:
                raise _error(
                    operation,
                    "min_length",
                    "must be less than or equal to max_length",
                    clean_min_length,
                )
        if (
            clean_method.startswith("netgen_")
            and clean_min_length is not None
            and clean_max_length is not None
            and clean_min_length > clean_max_length
        ):
            raise _error(
                operation,
                "min_length",
                "must be less than or equal to max_length",
                clean_min_length,
            )
        if clean_method == "standard":
            clean_linear_deflection = _number(
                operation,
                "linear_deflection",
                0.1 if linear_deflection is None else linear_deflection,
                minimum=0.0,
                maximum=_MAX_LENGTH,
                strict_minimum=True,
            )
            clean_angular_deflection = _number(
                operation,
                "angular_deflection_degrees",
                15.0
                if angular_deflection_degrees is None
                else angular_deflection_degrees,
                minimum=0.0,
                maximum=180.0,
                strict_minimum=True,
            )
            clean_relative = _boolean(
                operation, "relative", False if relative is None else relative
            )
            clean_groups = _boolean(
                operation,
                "preserve_face_groups",
                False if preserve_face_groups is None else preserve_face_groups,
            )
        else:
            clean_linear_deflection = None
            clean_angular_deflection = None
            clean_relative = None
            clean_groups = None

        is_netgen = clean_method.startswith("netgen_")
        if is_netgen:
            clean_second_order = _boolean(
                operation,
                "second_order",
                False if second_order is None else second_order,
            )
            clean_optimize = _boolean(
                operation, "optimize", True if optimize is None else optimize
            )
            clean_allow_quad = _boolean(
                operation, "allow_quad", False if allow_quad is None else allow_quad
            )
        else:
            clean_second_order = None
            clean_optimize = None
            clean_allow_quad = None
        if clean_second_order and clean_allow_quad:
            raise _error(
                operation,
                "second_order/allow_quad",
                "cannot both be true in the native Netgen mesher",
            )
        clean_fineness = None
        if clean_method == "netgen_fineness":
            clean_fineness = _enum(
                operation,
                "fineness",
                "moderate" if fineness is None else fineness,
                _NETGEN_FINENESS,
            )
        clean_growth_rate = None
        clean_segments_per_edge = None
        clean_segments_per_radius = None
        if clean_method == "netgen_custom":
            clean_growth_rate = _number(
                operation,
                "growth_rate",
                0.3 if growth_rate is None else growth_rate,
                minimum=0.1,
                maximum=1.0,
            )
            clean_segments_per_edge = _number(
                operation,
                "segments_per_edge",
                1.0 if segments_per_edge is None else segments_per_edge,
                minimum=0.0,
                maximum=1_000_000.0,
                strict_minimum=True,
            )
            clean_segments_per_radius = _number(
                operation,
                "segments_per_radius",
                2.0 if segments_per_radius is None else segments_per_radius,
                minimum=0.0,
                maximum=1_000_000.0,
                strict_minimum=True,
            )
        return DomainValue(
            domain=self.domain,
            operation=operation,
            output_type="mesh",
            arguments=(_reference(operation, "source", source),),
            properties={
                "subelements": _subelements(subelements),
                "method": clean_method,
                "linear_deflection": clean_linear_deflection,
                "angular_deflection_degrees": clean_angular_deflection,
                "relative": clean_relative,
                "preserve_face_groups": clean_groups,
                "max_length": clean_max_length,
                "max_area": clean_max_area,
                "local_length": clean_local_length,
                "deflection": clean_deflection,
                "min_length": clean_min_length,
                "fineness": clean_fineness,
                "growth_rate": clean_growth_rate,
                "segments_per_edge": clean_segments_per_edge,
                "segments_per_radius": clean_segments_per_radius,
                "second_order": clean_second_order,
                "optimize": clean_optimize,
                "allow_quad": clean_allow_quad,
                "label": _label(operation, label),
            },
        )

    def shape_from_mesh(
        self,
        source: Mapping[str, str],
        *,
        output_type: str = "shell",
        representation: str | None = None,
        facet_indices: Sequence[int] | None = None,
        segment_index: int | None = None,
        tolerance: float | None = None,
        harmonize_normals: bool = False,
        refine: bool | None = None,
        require_closed: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Convert all or part of one authenticated mesh to typed OCC topology."""

        operation = "shape_from_mesh"
        clean_output_type = _enum(
            operation,
            "output_type",
            output_type,
            _OUTPUT_TYPES[1:],
        )
        inferred_representation = (
            "boundary" if clean_output_type == "wire" else "surface"
        )
        clean_representation = (
            inferred_representation
            if representation is None
            else _enum(
                operation,
                "representation",
                representation,
                ("surface", "boundary"),
            )
        )
        allowed = (
            _SURFACE_OUTPUT_TYPES
            if clean_representation == "surface"
            else _BOUNDARY_OUTPUT_TYPES
        )
        if clean_output_type not in allowed:
            raise _error(
                operation,
                "output_type",
                f"must be one of {sorted(allowed)!r} when representation={clean_representation!r}",
                output_type,
            )
        clean_facets = _facet_indices(facet_indices)
        if segment_index is not None and clean_facets is not None:
            raise _error(
                operation,
                "facet_indices/segment_index",
                "are mutually exclusive selection modes",
            )
        if segment_index is not None and (
            isinstance(segment_index, bool)
            or type(segment_index) is not int
            or not 1 <= segment_index <= _MAX_SELECTION_ITEMS
        ):
            raise _error(
                operation,
                "segment_index",
                "must be null or a positive 1-based segment index",
                segment_index,
            )
        clean_harmonize = _boolean(operation, "harmonize_normals", harmonize_normals)
        clean_refine = (
            clean_representation == "surface"
            if refine is None
            else _boolean(operation, "refine", refine)
        )
        clean_closed = _boolean(operation, "require_closed", require_closed)
        if clean_representation == "boundary" and clean_harmonize:
            raise _error(
                operation,
                "harmonize_normals",
                "does not affect boundary extraction and must remain false",
                harmonize_normals,
            )
        if clean_representation == "boundary" and clean_refine:
            raise _error(
                operation,
                "refine",
                "is only defined for converted surface topology",
                refine,
            )
        if clean_representation == "boundary" and clean_closed:
            raise _error(
                operation,
                "require_closed",
                "applies to surface shells, not boundary wires",
                require_closed,
            )
        if clean_representation == "boundary" and tolerance is not None:
            raise _error(
                operation,
                "tolerance",
                "is used only for surface conversion and must be omitted for boundary extraction",
                tolerance,
            )
        if clean_output_type == "solid":
            clean_closed = True
        clean_tolerance = None
        if clean_representation == "surface":
            clean_tolerance = _number(
                operation,
                "tolerance",
                0.01 if tolerance is None else tolerance,
                minimum=1.0e-9,
                maximum=1_000_000.0,
            )
        return DomainValue(
            domain=self.domain,
            operation=operation,
            output_type=clean_output_type,
            arguments=(_reference(operation, "source", source),),
            properties={
                "representation": clean_representation,
                "facet_indices": clean_facets,
                "segment_index": segment_index,
                "tolerance": clean_tolerance,
                "harmonize_normals": clean_harmonize,
                "refine": clean_refine,
                "require_closed": clean_closed,
                "label": _label(operation, label),
            },
        )
