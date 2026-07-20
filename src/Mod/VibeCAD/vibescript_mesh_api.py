# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Mesh VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = ("mesh", "from_object", "transform", "repair", "diagnostics")
_OUTPUT_TYPES = ("mesh",)
_MAX_FACETS = 200_000
_MAX_COORDINATE = 1_000_000_000.0
_MAX_LABEL_CHARS = 256
_EPSILON = 1.0e-12
_MISSING = object()
_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MeshAPIError(ValueError):
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
    operation: str, parameter: str, reason: str, value: Any = _MISSING
) -> MeshAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return MeshAPIError(
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


def _integer(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise _error(operation, parameter, "must be an integer", value)
    if not minimum <= value <= maximum:
        raise _error(
            operation,
            parameter,
            f"must be in the inclusive range {minimum}-{maximum}",
            value,
        )
    return value


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
            "must be a stable input reference containing exactly document_uid and object_name",
            type(value).__name__,
        )
    document_uid = value.get("document_uid")
    object_name = value.get("object_name")
    if (
        not isinstance(document_uid, str)
        or not document_uid
        or document_uid != document_uid.strip()
        or len(document_uid) > 256
        or "\0" in document_uid
    ):
        raise _error(
            operation,
            f"{parameter}.document_uid",
            "must be a non-empty string of at most 256 characters without whitespace padding or nulls",
            document_uid,
        )
    if (
        not isinstance(object_name, str)
        or not _OBJECT_NAME.fullmatch(object_name)
        or len(object_name) > 256
    ):
        raise _error(
            operation,
            f"{parameter}.object_name",
            "must be a stable internal object name using letters, digits, and underscores",
            object_name,
        )
    return {"document_uid": document_uid, "object_name": object_name}


def _vector3(operation: str, parameter: str, value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "must be [x, y, z]", value)
    return tuple(
        _number(
            operation,
            f"{parameter}[{index}]",
            item,
            minimum=-_MAX_COORDINATE,
            maximum=_MAX_COORDINATE,
        )
        for index, item in enumerate(value)
    )


def _quaternion(operation: str, value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise _error(operation, "rotation", "must be quaternion [x, y, z, w]", value)
    clean = tuple(
        _number(operation, f"rotation[{index}]", item)
        for index, item in enumerate(value)
    )
    magnitude = math.hypot(*clean)
    if magnitude <= _EPSILON:
        raise _error(operation, "rotation", "quaternion must be non-zero")
    return tuple(item / magnitude for item in clean)


def _triangles(value: Any) -> tuple[tuple[tuple[float, float, float], ...], ...]:
    if not isinstance(value, (list, tuple)):
        raise _error(
            "mesh",
            "triangles",
            f"must contain 1-{_MAX_FACETS} triangles",
            type(value).__name__,
        )
    if not 1 <= len(value) <= _MAX_FACETS:
        raise _error(
            "mesh",
            "triangles",
            f"must contain 1-{_MAX_FACETS} triangles",
            f"{len(value)} triangles",
        )
    result = []
    for facet_index, triangle in enumerate(value):
        if not isinstance(triangle, (list, tuple)) or len(triangle) != 3:
            raise _error(
                "mesh",
                f"triangles[{facet_index}]",
                "must contain exactly three [x, y, z] vertices",
                triangle,
            )
        result.append(
            tuple(
                _vector3(
                    "mesh",
                    f"triangles[{facet_index}][{vertex_index}]",
                    vertex,
                )
                for vertex_index, vertex in enumerate(triangle)
            )
        )
    return tuple(result)


def _mesh_value(operation: str, parameter: str, value: Any) -> DomainValue:
    if (
        not isinstance(value, DomainValue)
        or value.domain != "mesh"
        or value.output_type != "mesh"
        or value.operation not in _EXPORTS
    ):
        raise _error(
            operation,
            parameter,
            "must be a value returned by this Mesh api",
            type(value).__name__,
        )
    return value


class MeshDomainAPI:
    """Immutable native-Mesh graph API injected into Mesh source."""

    __slots__ = ()

    domain = "mesh"
    exported_names = _EXPORTS

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        if tuple(dict.fromkeys(str(item) for item in exports)) != _EXPORTS:
            raise RuntimeError(f"Mesh pack exports must be exactly {_EXPORTS!r}.")
        if tuple(dict.fromkeys(str(item) for item in output_types)) != _OUTPUT_TYPES:
            raise RuntimeError(
                f"Mesh pack output types must be exactly {_OUTPUT_TYPES!r}."
            )

    def mesh(
        self,
        triangles: Sequence[Sequence[Sequence[float]]],
        *,
        label: str = "",
    ) -> DomainValue:
        """Create a native triangle mesh; defects remain available to api.repair."""

        return DomainValue(
            domain=self.domain,
            operation="mesh",
            output_type="mesh",
            arguments=(_triangles(triangles),),
            properties={"label": _label("mesh", label)},
        )

    def from_object(
        self,
        reference: Mapping[str, str],
        *,
        label: str = "",
    ) -> DomainValue:
        """Acquire one placement-baked snapshot of a referenced Mesh::Feature."""

        return DomainValue(
            domain=self.domain,
            operation="from_object",
            output_type="mesh",
            arguments=(_reference("from_object", "reference", reference),),
            properties={"label": _label("from_object", label)},
        )

    def transform(
        self,
        source: DomainValue,
        *,
        translation: Sequence[float] = (0.0, 0.0, 0.0),
        rotation: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
        scale: Sequence[float] = (1.0, 1.0, 1.0),
        label: str = "",
    ) -> DomainValue:
        """Bake a translation, normalized quaternion rotation, and positive scale."""

        clean_scale = _vector3("transform", "scale", scale)
        if any(value <= 0.0 for value in clean_scale):
            raise _error("transform", "scale", "values must be greater than 0", scale)
        return DomainValue(
            domain=self.domain,
            operation="transform",
            output_type="mesh",
            arguments=(_mesh_value("transform", "source", source),),
            properties={
                "translation": _vector3("transform", "translation", translation),
                "rotation": _quaternion("transform", rotation),
                "scale": clean_scale,
                "label": _label("transform", label),
            },
        )

    def repair(
        self,
        source: DomainValue,
        *,
        remove_duplicate_points: bool = False,
        remove_duplicate_facets: bool = False,
        fix_degenerations: bool = False,
        remove_non_manifolds: bool = False,
        fix_self_intersections: bool = False,
        fill_holes_max_edges: int = 0,
        harmonize_normals: bool = False,
        decimate_reduction: float = 0.0,
        decimate_tolerance: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Run an explicit ordered set of native Mesh repair operations."""

        reduction = _number(
            "repair",
            "decimate_reduction",
            decimate_reduction,
            minimum=0.0,
            maximum=1.0,
        )
        tolerance = _number(
            "repair",
            "decimate_tolerance",
            decimate_tolerance,
            minimum=0.0,
            maximum=_MAX_COORDINATE,
        )
        if (reduction == 0.0) != (tolerance == 0.0):
            raise _error(
                "repair",
                "decimate_reduction/decimate_tolerance",
                "must both be zero or both be greater than zero",
            )
        hole_limit = _integer(
            "repair",
            "fill_holes_max_edges",
            fill_holes_max_edges,
            minimum=0,
            maximum=_MAX_FACETS * 3,
        )
        repair_flags = {
            "remove_duplicate_points": _boolean(
                "repair", "remove_duplicate_points", remove_duplicate_points
            ),
            "remove_duplicate_facets": _boolean(
                "repair", "remove_duplicate_facets", remove_duplicate_facets
            ),
            "fix_degenerations": _boolean(
                "repair", "fix_degenerations", fix_degenerations
            ),
            "remove_non_manifolds": _boolean(
                "repair", "remove_non_manifolds", remove_non_manifolds
            ),
            "fix_self_intersections": _boolean(
                "repair", "fix_self_intersections", fix_self_intersections
            ),
            "harmonize_normals": _boolean(
                "repair", "harmonize_normals", harmonize_normals
            ),
        }
        if not any(repair_flags.values()) and hole_limit == 0 and reduction == 0.0:
            raise _error(
                "repair",
                "operations",
                "must enable at least one explicit repair operation",
            )
        return DomainValue(
            domain=self.domain,
            operation="repair",
            output_type="mesh",
            arguments=(_mesh_value("repair", "source", source),),
            properties={
                **repair_flags,
                "fill_holes_max_edges": hole_limit,
                "decimate_reduction": reduction,
                "decimate_tolerance": tolerance,
                "label": _label("repair", label),
            },
        )

    def diagnostics(
        self,
        source: DomainValue,
        *,
        require_solid: bool = False,
        require_closed: bool = False,
        require_manifold: bool = False,
        require_consistent_orientation: bool = False,
        require_no_self_intersections: bool = False,
        max_components: int | None = None,
        max_open_edges: int | None = None,
        label: str = "",
    ) -> DomainValue:
        """Inspect native topology and reject the candidate when requirements fail."""

        clean_components = (
            None
            if max_components is None
            else _integer(
                "diagnostics",
                "max_components",
                max_components,
                minimum=1,
                maximum=_MAX_FACETS,
            )
        )
        clean_open_edges = (
            None
            if max_open_edges is None
            else _integer(
                "diagnostics",
                "max_open_edges",
                max_open_edges,
                minimum=0,
                maximum=_MAX_FACETS * 3,
            )
        )
        return DomainValue(
            domain=self.domain,
            operation="diagnostics",
            output_type="mesh",
            arguments=(_mesh_value("diagnostics", "source", source),),
            properties={
                "require_solid": _boolean(
                    "diagnostics", "require_solid", require_solid
                ),
                "require_closed": _boolean(
                    "diagnostics", "require_closed", require_closed
                ),
                "require_manifold": _boolean(
                    "diagnostics", "require_manifold", require_manifold
                ),
                "require_consistent_orientation": _boolean(
                    "diagnostics",
                    "require_consistent_orientation",
                    require_consistent_orientation,
                ),
                "require_no_self_intersections": _boolean(
                    "diagnostics",
                    "require_no_self_intersections",
                    require_no_self_intersections,
                ),
                "max_components": clean_components,
                "max_open_edges": clean_open_edges,
                "label": _label("diagnostics", label),
            },
        )
