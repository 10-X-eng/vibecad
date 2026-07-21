# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical immutable API for Reverse Engineering VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = (
    "fit_curve",
    "fit_surface",
    "reconstruct",
    "segment",
    "fit_metrics",
)
_OUTPUT_TYPES = ("curve", "surface", "brep", "mesh", "fit_metrics")
_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_LABEL_CHARS = 256
_MAX_INLINE_POINTS = 100_000
_MAX_COORDINATE = 1.0e12
_MISSING = object()


class ReverseEngineeringAPIError(ValueError):
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
) -> ReverseEngineeringAPIError:
    if value is _MISSING:
        suffix = ""
    elif isinstance(value, (list, tuple)) and len(value) > 12:
        suffix = f"; received {type(value).__name__} with {len(value)} items"
    elif isinstance(value, str) and len(value) > 256:
        suffix = f"; received string with {len(value)} characters"
    else:
        suffix = f"; received {value!r}"
    return ReverseEngineeringAPIError(
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
    minimum: float,
    maximum: float,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(operation, parameter, "must be a finite number", value)
    clean = float(value)
    if not math.isfinite(clean):
        raise _error(operation, parameter, "must be finite", value)
    if clean < minimum or (strict_minimum and clean == minimum):
        relation = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    if clean > maximum:
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
            f"must be between {minimum} and {maximum} inclusive",
            value,
        )
    return value


def _boolean(operation: str, parameter: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise _error(operation, parameter, "must be true or false", value)
    return value


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


def _label(operation: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > _MAX_LABEL_CHARS or "\0" in value:
        raise _error(
            operation,
            "label",
            f"must be a string of at most {_MAX_LABEL_CHARS} characters without nulls",
            value,
        )
    return value


def _vector(operation: str, parameter: str, value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "must be a three-number [x, y, z] array", value)
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


def _reference(operation: str, value: Mapping[str, Any]) -> dict[str, str]:
    result = {}
    for name in ("document_uid", "object_name"):
        raw = value.get(name)
        if (
            not isinstance(raw, str)
            or not raw
            or raw != raw.strip()
            or len(raw) > 256
            or "\0" in raw
        ):
            raise _error(
                operation,
                f"source.{name}",
                "must be a non-empty string of at most 256 characters without "
                "surrounding whitespace or nulls",
                raw,
            )
        result[name] = raw
    return result


def _point_source(operation: str, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        fields = set(value)
        if fields == {"document_uid", "object_name"}:
            return {
                "kind": "document",
                "reference": _reference(operation, value),
                "artifact_id": None,
                "points": None,
            }
        if fields == {"artifact_id"}:
            artifact_id = value.get("artifact_id")
            if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(
                artifact_id
            ):
                raise _error(
                    operation,
                    "source.artifact_id",
                    "must be a 32-character lowercase hexadecimal project artifact id",
                    artifact_id,
                )
            return {
                "kind": "artifact",
                "reference": None,
                "artifact_id": artifact_id,
                "points": None,
            }
        raise _error(
            operation,
            "source",
            "object must contain exactly artifact_id or exactly document_uid and object_name",
            sorted(str(field) for field in fields),
        )
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(
            operation,
            "source",
            "must be a stable point/mesh reference, approved artifact reference, or point array",
            value,
        )
    if not 2 <= len(value) <= _MAX_INLINE_POINTS:
        raise _error(
            operation,
            "source",
            f"inline point array must contain 2-{_MAX_INLINE_POINTS} points",
            value,
        )
    return {
        "kind": "inline",
        "reference": None,
        "artifact_id": None,
        "points": tuple(
            _vector(operation, f"source[{index}]", point)
            for index, point in enumerate(value)
        ),
    }


def _mesh_source(value: Any) -> Any:
    if isinstance(value, DomainValue):
        if value.domain != "reverse_engineering" or value.output_type != "mesh":
            raise _error(
                "segment",
                "source",
                "domain value must be a Reverse Engineering mesh",
                value.output_type,
            )
        if value.operation not in {"reconstruct", "segment"}:
            raise _error(
                "segment",
                "source",
                "mesh domain value must come from reconstruct or segment",
                value.operation,
            )
        return value
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _error(
            "segment",
            "source",
            "must be a stable mesh reference or Reverse Engineering mesh value",
            value,
        )
    return {
        "kind": "document",
        "reference": _reference("segment", value),
    }


def _uv_directions(value: Any) -> tuple[tuple[float, float, float], ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _error(
            "fit_surface",
            "uv_directions",
            "must be null or [u_direction, v_direction]",
            value,
        )
    u_direction = _vector("fit_surface", "uv_directions[0]", value[0])
    v_direction = _vector("fit_surface", "uv_directions[1]", value[1])
    u_length = math.sqrt(sum(component * component for component in u_direction))
    v_length = math.sqrt(sum(component * component for component in v_direction))
    if u_length <= 1.0e-12 or v_length <= 1.0e-12:
        raise _error(
            "fit_surface",
            "uv_directions",
            "must contain two non-zero directions",
            value,
        )
    cosine = sum(a * b for a, b in zip(u_direction, v_direction)) / (
        u_length * v_length
    )
    if abs(cosine) >= 1.0 - 1.0e-9:
        raise _error(
            "fit_surface",
            "uv_directions",
            "must not be parallel",
            value,
        )
    return (u_direction, v_direction)


def _parameter_object(
    operation: str,
    value: Any,
    *,
    allowed: set[str],
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _error(operation, "parameters", "must be null or an object", value)
    unexpected = set(value) - allowed
    if unexpected:
        raise _error(
            operation,
            "parameters",
            f"contains fields unused by the selected method: {sorted(unexpected)!r}",
        )
    return dict(value)


def _grid_size(operation: str, value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _error(operation, "parameters.grid_size", "must be [width, height]", value)
    return (
        _integer(operation, "parameters.grid_size[0]", value[0], minimum=2, maximum=10_000),
        _integer(operation, "parameters.grid_size[1]", value[1], minimum=2, maximum=10_000),
    )


def _reconstruction_parameters(method: str, value: Any) -> dict[str, Any]:
    operation = "reconstruct"
    if method == "structured_grid":
        raw = _parameter_object(
            operation,
            value,
            allowed={"grid_size", "diagonal"},
        )
        grid_size = _grid_size(operation, raw["grid_size"]) if "grid_size" in raw else None
        canonical = {
            "diagonal": _enum(
                operation,
                "parameters.diagonal",
                raw.get("diagonal", "shortest"),
                ("shortest", "forward", "backward"),
            ),
        }
        if grid_size is not None:
            canonical["grid_size"] = grid_size
        return canonical
    if method == "greedy":
        raw = _parameter_object(
            operation,
            value,
            allowed={"search_radius", "mu", "k_search"},
        )
        if "search_radius" not in raw:
            raise _error(
                operation,
                "parameters.search_radius",
                "is required by method 'greedy'",
            )
        return {
            "search_radius": _number(
                operation,
                "parameters.search_radius",
                raw["search_radius"],
                minimum=0.0,
                maximum=1.0e9,
                strict_minimum=True,
            ),
            "mu": _number(
                operation,
                "parameters.mu",
                raw.get("mu", 2.5),
                minimum=0.0,
                maximum=1000.0,
                strict_minimum=True,
            ),
            "k_search": _integer(
                operation,
                "parameters.k_search",
                raw.get("k_search", 10),
                minimum=3,
                maximum=1024,
            ),
        }
    raw = _parameter_object(
        operation,
        value,
        allowed={"k_search", "octree_depth", "solver_divide", "samples_per_node"},
    )
    return {
        "k_search": _integer(
            operation,
            "parameters.k_search",
            raw.get("k_search", 10),
            minimum=3,
            maximum=1024,
        ),
        "octree_depth": _integer(
            operation,
            "parameters.octree_depth",
            raw.get("octree_depth", 8),
            minimum=4,
            maximum=16,
        ),
        "solver_divide": _integer(
            operation,
            "parameters.solver_divide",
            raw.get("solver_divide", 8),
            minimum=4,
            maximum=16,
        ),
        "samples_per_node": _number(
            operation,
            "parameters.samples_per_node",
            raw.get("samples_per_node", 1.5),
            minimum=0.0,
            maximum=100.0,
            strict_minimum=True,
        ),
    }


def _segment_selector(operation: str, value: Any) -> str | int:
    if isinstance(value, str):
        return _enum(operation, "parameters.segment", value, ("all",))
    return _integer(
        operation,
        "parameters.segment",
        value,
        minimum=0,
        maximum=1_000_000,
    )


def _segmentation_parameters(method: str, value: Any) -> dict[str, Any]:
    operation = "segment"
    common = {"segment", "minimum_facets"}
    allowed = set(common)
    if method == "normal_regions":
        allowed.add("angle_degrees")
    elif method in {"native_region_growing", "native_feature"}:
        allowed.add("k_search")
    raw = _parameter_object(operation, value, allowed=allowed)
    canonical = {
        "segment": _segment_selector(operation, raw.get("segment", "all")),
        "minimum_facets": _integer(
            operation,
            "parameters.minimum_facets",
            raw.get("minimum_facets", 1),
            minimum=1,
            maximum=5_000_000,
        ),
    }
    if method == "normal_regions":
        canonical["angle_degrees"] = _number(
            operation,
            "parameters.angle_degrees",
            raw.get("angle_degrees", 15.0),
            minimum=0.0,
            maximum=180.0,
            strict_minimum=True,
        )
    elif method in {"native_region_growing", "native_feature"}:
        canonical["k_search"] = _integer(
            operation,
            "parameters.k_search",
            raw.get("k_search", 10),
            minimum=3,
            maximum=1024,
        )
    return canonical


class ReverseEngineeringDomainAPI:
    """One immutable fitting, reconstruction, segmentation, and metrics API."""

    __slots__ = ()

    domain = "reverse_engineering"
    exported_names = _EXPORTS

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        if tuple(dict.fromkeys(str(item) for item in exports)) != _EXPORTS:
            raise RuntimeError(
                f"Reverse Engineering pack exports must be exactly {_EXPORTS!r}."
            )
        if tuple(dict.fromkeys(str(item) for item in output_types)) != _OUTPUT_TYPES:
            raise RuntimeError(
                f"Reverse Engineering output types must be exactly {_OUTPUT_TYPES!r}."
            )

    def fit_curve(
        self,
        source: Any,
        *,
        closed: bool = False,
        parametrization: str = "chord_length",
        min_degree: int = 3,
        max_degree: int = 8,
        continuity: str = "c2",
        tolerance: float = 0.001,
        label: str = "",
    ) -> DomainValue:
        """Fit one native B-spline curve to authenticated source points."""

        closed = _boolean("fit_curve", "closed", closed)
        source_value = _point_source("fit_curve", source)
        if source_value["kind"] == "inline" and len(source_value["points"]) < (
            3 if closed else 2
        ):
            raise _error("fit_curve", "source", "contains too few points")
        min_degree = _integer(
            "fit_curve", "min_degree", min_degree, minimum=1, maximum=25
        )
        max_degree = _integer(
            "fit_curve", "max_degree", max_degree, minimum=1, maximum=25
        )
        if min_degree > max_degree:
            raise _error(
                "fit_curve",
                "min_degree",
                "must not exceed max_degree",
                min_degree,
            )
        return DomainValue(
            domain=self.domain,
            operation="fit_curve",
            output_type="curve",
            arguments=(source_value,),
            properties={
                "closed": closed,
                "parametrization": _enum(
                    "fit_curve",
                    "parametrization",
                    parametrization,
                    ("chord_length", "centripetal", "uniform"),
                ),
                "min_degree": min_degree,
                "max_degree": max_degree,
                "continuity": _enum(
                    "fit_curve", "continuity", continuity, ("c0", "c1", "c2")
                ),
                "tolerance": _number(
                    "fit_curve",
                    "tolerance",
                    tolerance,
                    minimum=0.0,
                    maximum=1.0e9,
                    strict_minimum=True,
                ),
                "label": _label("fit_curve", label),
            },
        )

    def fit_surface(
        self,
        source: Any,
        *,
        u_degree: int = 3,
        v_degree: int = 3,
        u_poles: int = 6,
        v_poles: int = 6,
        smooth: bool = True,
        smoothing_weight: float = 0.1,
        gradient_weight: float = 1.0,
        bending_weight: float = 0.0,
        curvature_weight: float = 0.0,
        iterations: int = 5,
        correction: bool = True,
        patch_factor: float = 1.0,
        uv_directions: Sequence[Sequence[float]] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Fit one native B-spline surface to authenticated point or mesh vertices."""

        u_degree = _integer(
            "fit_surface", "u_degree", u_degree, minimum=1, maximum=15
        )
        v_degree = _integer(
            "fit_surface", "v_degree", v_degree, minimum=1, maximum=15
        )
        u_poles = _integer(
            "fit_surface", "u_poles", u_poles, minimum=2, maximum=64
        )
        v_poles = _integer(
            "fit_surface", "v_poles", v_poles, minimum=2, maximum=64
        )
        if u_degree >= u_poles:
            raise _error("fit_surface", "u_degree", "must be smaller than u_poles")
        if v_degree >= v_poles:
            raise _error("fit_surface", "v_degree", "must be smaller than v_poles")
        source_value = _point_source("fit_surface", source)
        if (
            source_value["kind"] == "inline"
            and len(source_value["points"]) < u_poles * v_poles
        ):
            raise _error(
                "fit_surface",
                "source",
                f"must contain at least u_poles * v_poles ({u_poles * v_poles}) points",
            )
        return DomainValue(
            domain=self.domain,
            operation="fit_surface",
            output_type="surface",
            arguments=(source_value,),
            properties={
                "u_degree": u_degree,
                "v_degree": v_degree,
                "u_poles": u_poles,
                "v_poles": v_poles,
                "smooth": _boolean("fit_surface", "smooth", smooth),
                "smoothing_weight": _number(
                    "fit_surface",
                    "smoothing_weight",
                    smoothing_weight,
                    minimum=0.0,
                    maximum=1.0e9,
                ),
                "gradient_weight": _number(
                    "fit_surface",
                    "gradient_weight",
                    gradient_weight,
                    minimum=0.0,
                    maximum=1.0,
                ),
                "bending_weight": _number(
                    "fit_surface",
                    "bending_weight",
                    bending_weight,
                    minimum=0.0,
                    maximum=1.0,
                ),
                "curvature_weight": _number(
                    "fit_surface",
                    "curvature_weight",
                    curvature_weight,
                    minimum=0.0,
                    maximum=1.0,
                ),
                "iterations": _integer(
                    "fit_surface", "iterations", iterations, minimum=1, maximum=1000
                ),
                "correction": _boolean("fit_surface", "correction", correction),
                "patch_factor": _number(
                    "fit_surface",
                    "patch_factor",
                    patch_factor,
                    minimum=0.0,
                    maximum=1000.0,
                    strict_minimum=True,
                ),
                "uv_directions": _uv_directions(uv_directions),
                "label": _label("fit_surface", label),
            },
        )

    def reconstruct(
        self,
        source: Any,
        *,
        method: str = "structured_grid",
        output_type: str = "mesh",
        parameters: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Triangulate or reconstruct one cloud through a canonical method selector."""

        method = _enum(
            "reconstruct",
            "method",
            method,
            ("structured_grid", "greedy", "poisson"),
        )
        output_type = _enum(
            "reconstruct", "output_type", output_type, ("mesh", "brep")
        )
        return DomainValue(
            domain=self.domain,
            operation="reconstruct",
            output_type=output_type,
            arguments=(_point_source("reconstruct", source),),
            properties={
                "method": method,
                "parameters": _reconstruction_parameters(method, parameters),
                "label": _label("reconstruct", label),
            },
        )

    def segment(
        self,
        source: Any,
        *,
        method: str = "connected_components",
        parameters: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Segment a native mesh with portable or native optional algorithms."""

        method = _enum(
            "segment",
            "method",
            method,
            (
                "connected_components",
                "normal_regions",
                "native_region_growing",
                "native_feature",
            ),
        )
        return DomainValue(
            domain=self.domain,
            operation="segment",
            output_type="mesh",
            arguments=(_mesh_source(source),),
            properties={
                "method": method,
                "parameters": _segmentation_parameters(method, parameters),
                "label": _label("segment", label),
            },
        )

    def fit_metrics(
        self,
        target: DomainValue,
        *,
        tolerance: float = 0.1,
        label: str = "",
    ) -> DomainValue:
        """Publish the authenticated fit/coverage report for one returned result."""

        if (
            not isinstance(target, DomainValue)
            or target.domain != self.domain
            or target.output_type not in {"curve", "surface", "brep", "mesh"}
            or target.operation not in {
                "fit_curve",
                "fit_surface",
                "reconstruct",
                "segment",
            }
        ):
            raise _error(
                "fit_metrics",
                "target",
                "must be a fitted, reconstructed, or segmented Reverse Engineering value",
                getattr(target, "output_type", type(target).__name__),
            )
        return DomainValue(
            domain=self.domain,
            operation="fit_metrics",
            output_type="fit_metrics",
            arguments=(target,),
            properties={
                "tolerance": _number(
                    "fit_metrics",
                    "tolerance",
                    tolerance,
                    minimum=0.0,
                    maximum=1.0e9,
                    strict_minimum=True,
                ),
                "label": _label("fit_metrics", label),
            },
        )
