# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Points VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = ("point_cloud",)
_OUTPUT_TYPES = ("points",)
_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_LABEL_CHARS = 256
_MAX_INLINE_POINTS = 50_000
_MAX_PIPELINE_STAGES = 32
_MAX_COORDINATE = 1.0e12
_MAX_SCALE = 1.0e9
_MAX_SAMPLE_STEP = 1_000_000_000
_MISSING = object()


class PointsAPIError(ValueError):
    """A source error carrying one exact repair target for the operating model."""

    def __init__(
        self,
        message: str,
        *,
        parameter: str,
        reason: str,
    ) -> None:
        self.details = {
            "stage": "source_validation",
            "operation": "point_cloud",
            "parameter": parameter,
            "reason": reason,
            "correction": (
                f"Correct api.point_cloud parameter {parameter!r}: it {reason}. "
                "Change only the failing source expression, then retry against the "
                "failed working revision."
            ),
        }
        super().__init__(message)


def _error(
    parameter: str,
    reason: str,
    value: Any = _MISSING,
) -> PointsAPIError:
    if value is _MISSING:
        suffix = ""
    elif isinstance(value, (list, tuple)) and len(value) > 12:
        suffix = f"; received {type(value).__name__} with {len(value)} items"
    elif isinstance(value, str) and len(value) > 256:
        suffix = f"; received string with {len(value)} characters"
    else:
        suffix = f"; received {value!r}"
    return PointsAPIError(
        f"api.point_cloud: {parameter} {reason}{suffix}.",
        parameter=parameter,
        reason=reason,
    )


def _number(
    parameter: str,
    value: Any,
    *,
    minimum: float = -_MAX_COORDINATE,
    maximum: float = _MAX_COORDINATE,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(parameter, "must be a finite number", value)
    clean = float(value)
    if not math.isfinite(clean):
        raise _error(parameter, "must be finite", value)
    if clean < minimum or (strict_minimum and clean == minimum):
        relation = "greater than" if strict_minimum else "at least"
        raise _error(parameter, f"must be {relation} {minimum:g}", value)
    if clean > maximum:
        raise _error(parameter, f"must be at most {maximum:g}", value)
    return clean


def _integer(
    parameter: str,
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise _error(parameter, "must be an integer", value)
    if not minimum <= value <= maximum:
        raise _error(
            parameter,
            f"must be between {minimum} and {maximum} inclusive",
            value,
        )
    return value


def _enum(parameter: str, value: Any, choices: Iterable[str]) -> str:
    if not isinstance(value, str):
        raise _error(parameter, "must be a string enum", value)
    clean = value.strip().lower()
    allowed = tuple(choices)
    if clean not in allowed:
        raise _error(parameter, f"must be one of {list(allowed)!r}", value)
    return clean


def _vector(
    parameter: str,
    value: Any,
    *,
    minimum: float = -_MAX_COORDINATE,
    maximum: float = _MAX_COORDINATE,
    strict_minimum: bool = False,
) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(parameter, "must be a three-number [x, y, z] array", value)
    return tuple(
        _number(
            f"{parameter}[{index}]",
            item,
            minimum=minimum,
            maximum=maximum,
            strict_minimum=strict_minimum,
        )
        for index, item in enumerate(value)
    )


def _quaternion(parameter: str, value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise _error(
            parameter,
            "must be a four-number quaternion [x, y, z, w]",
            value,
        )
    values = tuple(
        _number(
            f"{parameter}[{index}]",
            item,
            minimum=-1.0e9,
            maximum=1.0e9,
        )
        for index, item in enumerate(value)
    )
    norm = math.sqrt(sum(item * item for item in values))
    if norm <= 1.0e-12:
        raise _error(parameter, "must have non-zero length", value)
    return tuple(item / norm for item in values)


def _label(value: Any) -> str:
    if not isinstance(value, str) or len(value) > _MAX_LABEL_CHARS or "\0" in value:
        raise _error(
            "label",
            f"must be a string of at most {_MAX_LABEL_CHARS} characters without nulls",
            value,
        )
    return value


def _stable_document_reference(value: Mapping[str, Any]) -> dict[str, str]:
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
                f"source.{name}",
                "must be a non-empty string of at most 256 characters without "
                "surrounding whitespace or nulls",
                raw,
            )
        result[name] = raw
    return result


def _source(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        fields = set(value)
        if fields == {"document_uid", "object_name"}:
            return {
                "kind": "document",
                "reference": _stable_document_reference(value),
                "artifact_id": None,
                "points": None,
            }
        if fields == {"artifact_id"}:
            artifact_id = value.get("artifact_id")
            if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(
                artifact_id
            ):
                raise _error(
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
            "source",
            "object must contain exactly artifact_id or exactly document_uid and object_name",
            sorted(str(field) for field in fields),
        )
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(
            "source",
            "must be a stable document reference, approved artifact reference, or point array",
            value,
        )
    if not value or len(value) > _MAX_INLINE_POINTS:
        raise _error(
            "source",
            f"inline point array must contain 1-{_MAX_INLINE_POINTS} points",
            value,
        )
    points = tuple(
        _vector(f"source[{index}]", point)
        for index, point in enumerate(value)
    )
    return {
        "kind": "inline",
        "reference": None,
        "artifact_id": None,
        "points": points,
    }


def _transform_stage(stage: Mapping[str, Any], index: int) -> dict[str, Any]:
    allowed = {"op", "translation", "rotation", "scale"}
    unexpected = set(stage) - allowed
    if unexpected:
        raise _error(
            f"pipeline[{index}]",
            f"contains unexpected fields {sorted(unexpected)!r}",
        )
    translation = _vector(
        f"pipeline[{index}].translation",
        stage.get("translation", (0.0, 0.0, 0.0)),
    )
    rotation = _quaternion(
        f"pipeline[{index}].rotation",
        stage.get("rotation", (0.0, 0.0, 0.0, 1.0)),
    )
    scale = _vector(
        f"pipeline[{index}].scale",
        stage.get("scale", (1.0, 1.0, 1.0)),
        minimum=0.0,
        maximum=_MAX_SCALE,
        strict_minimum=True,
    )
    identity_rotation = all(
        math.isclose(first, second, rel_tol=0.0, abs_tol=1.0e-15)
        for first, second in zip(rotation, (0.0, 0.0, 0.0, 1.0))
    )
    if translation == (0.0, 0.0, 0.0) and identity_rotation and scale == (
        1.0,
        1.0,
        1.0,
    ):
        raise _error(
            f"pipeline[{index}]",
            "is an identity transform and should be removed",
        )
    return {
        "op": "transform",
        "method": None,
        "translation": translation,
        "rotation": rotation,
        "scale": scale,
        "minimum": None,
        "maximum": None,
        "tolerance": None,
        "voxel_size": None,
        "reduction": None,
        "step": None,
        "offset": None,
        "max_points": None,
    }


def _filter_stage(stage: Mapping[str, Any], index: int) -> dict[str, Any]:
    method = _enum(
        f"pipeline[{index}].method",
        stage.get("method"),
        ("crop_box", "deduplicate"),
    )
    common = {"op", "method"}
    if method == "crop_box":
        allowed = common | {"minimum", "maximum"}
        unexpected = set(stage) - allowed
        if unexpected:
            raise _error(
                f"pipeline[{index}]",
                f"contains fields unused by filter method {method!r}: {sorted(unexpected)!r}",
            )
        if "minimum" not in stage or "maximum" not in stage:
            raise _error(
                f"pipeline[{index}].minimum/maximum",
                "are both required for filter method 'crop_box'",
            )
        minimum = _vector(f"pipeline[{index}].minimum", stage["minimum"])
        maximum = _vector(f"pipeline[{index}].maximum", stage["maximum"])
        for axis, (low, high) in enumerate(zip(minimum, maximum)):
            if low >= high:
                raise _error(
                    f"pipeline[{index}].minimum[{axis}]",
                    "must be less than the corresponding maximum",
                    low,
                )
        tolerance = None
    else:
        allowed = common | {"tolerance"}
        unexpected = set(stage) - allowed
        if unexpected:
            raise _error(
                f"pipeline[{index}]",
                f"contains fields unused by filter method {method!r}: {sorted(unexpected)!r}",
            )
        if "tolerance" not in stage:
            raise _error(
                f"pipeline[{index}].tolerance",
                "is required for filter method 'deduplicate'",
            )
        tolerance = _number(
            f"pipeline[{index}].tolerance",
            stage["tolerance"],
            minimum=0.0,
            maximum=_MAX_COORDINATE,
            strict_minimum=True,
        )
        minimum = None
        maximum = None
    return {
        "op": "filter",
        "method": method,
        "translation": None,
        "rotation": None,
        "scale": None,
        "minimum": minimum,
        "maximum": maximum,
        "tolerance": tolerance,
        "voxel_size": None,
        "reduction": None,
        "step": None,
        "offset": None,
        "max_points": None,
    }


def _sample_stage(stage: Mapping[str, Any], index: int) -> dict[str, Any]:
    method = _enum(
        f"pipeline[{index}].method",
        stage.get("method"),
        ("voxel", "stride", "limit"),
    )
    common = {"op", "method"}
    voxel_size = None
    reduction = None
    step = None
    offset = None
    max_points = None
    if method == "voxel":
        allowed = common | {"voxel_size", "reduction"}
        if "voxel_size" not in stage:
            raise _error(
                f"pipeline[{index}].voxel_size",
                "is required for sample method 'voxel'",
            )
        voxel_size = _number(
            f"pipeline[{index}].voxel_size",
            stage["voxel_size"],
            minimum=0.0,
            maximum=_MAX_COORDINATE,
            strict_minimum=True,
        )
        reduction = _enum(
            f"pipeline[{index}].reduction",
            stage.get("reduction", "first"),
            ("first", "centroid"),
        )
    elif method == "stride":
        allowed = common | {"step", "offset"}
        if "step" not in stage:
            raise _error(
                f"pipeline[{index}].step",
                "is required for sample method 'stride'",
            )
        step = _integer(
            f"pipeline[{index}].step",
            stage["step"],
            minimum=2,
            maximum=_MAX_SAMPLE_STEP,
        )
        offset = _integer(
            f"pipeline[{index}].offset",
            stage.get("offset", 0),
            minimum=0,
            maximum=step - 1,
        )
    else:
        allowed = common | {"max_points"}
        if "max_points" not in stage:
            raise _error(
                f"pipeline[{index}].max_points",
                "is required for sample method 'limit'",
            )
        max_points = _integer(
            f"pipeline[{index}].max_points",
            stage["max_points"],
            minimum=1,
            maximum=_MAX_SAMPLE_STEP,
        )
    unexpected = set(stage) - allowed
    if unexpected:
        raise _error(
            f"pipeline[{index}]",
            f"contains fields unused by sample method {method!r}: {sorted(unexpected)!r}",
        )
    return {
        "op": "sample",
        "method": method,
        "translation": None,
        "rotation": None,
        "scale": None,
        "minimum": None,
        "maximum": None,
        "tolerance": None,
        "voxel_size": voxel_size,
        "reduction": reduction,
        "step": step,
        "offset": offset,
        "max_points": max_points,
    }


def _pipeline(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise _error("pipeline", "must be null or an ordered array of stages", value)
    if len(value) > _MAX_PIPELINE_STAGES:
        raise _error(
            "pipeline",
            f"may contain at most {_MAX_PIPELINE_STAGES} stages",
            value,
        )
    result = []
    for index, stage in enumerate(value):
        if not isinstance(stage, Mapping):
            raise _error(f"pipeline[{index}]", "must be an object", stage)
        operation = _enum(
            f"pipeline[{index}].op",
            stage.get("op"),
            ("transform", "filter", "sample"),
        )
        if operation == "transform":
            clean = _transform_stage(stage, index)
        elif operation == "filter":
            clean = _filter_stage(stage, index)
        else:
            clean = _sample_stage(stage, index)
        result.append(clean)
    return tuple(result)


class PointsDomainAPI:
    """Immutable point-cloud ingestion and processing API."""

    __slots__ = ()

    domain = "points"
    exported_names = _EXPORTS

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        if tuple(dict.fromkeys(str(item) for item in exports)) != _EXPORTS:
            raise RuntimeError(f"Points pack exports must be exactly {_EXPORTS!r}.")
        if tuple(dict.fromkeys(str(item) for item in output_types)) != _OUTPUT_TYPES:
            raise RuntimeError(
                f"Points pack output types must be exactly {_OUTPUT_TYPES!r}."
            )

    def point_cloud(
        self,
        source: Any,
        *,
        pipeline: Sequence[Mapping[str, Any]] | None = None,
        invalid_points: str = "reject",
        preserve_attributes: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Load one authenticated source and apply one ordered canonical pipeline."""

        if not isinstance(preserve_attributes, bool):
            raise _error(
                "preserve_attributes",
                "must be true or false",
                preserve_attributes,
            )
        return DomainValue(
            domain=self.domain,
            operation="point_cloud",
            output_type="points",
            arguments=(_source(source),),
            properties={
                "pipeline": _pipeline(pipeline),
                "invalid_points": _enum(
                    "invalid_points",
                    invalid_points,
                    ("reject", "drop"),
                ),
                "preserve_attributes": preserve_attributes,
                "label": _label(label),
            },
        )
