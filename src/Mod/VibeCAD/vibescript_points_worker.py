# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native point-cloud processor for the Points VibeScript domain."""

from __future__ import annotations

from array import array
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_points_api import PointsDomainAPI


VALIDATION_SCHEMA = "vibecad-vibescript-points-validation-v1"
ATTRIBUTE_SCHEMA = "vibecad-vibescript-point-attributes-f32le-v1"
_EXPORTS = ("point_cloud",)
_OUTPUT_TYPES = ("points",)
_MAX_DEFINITION_BYTES = 1_000_000
_MAX_POINTS = 2_000_000
_MAX_DEDUPLICATE_POINTS = 500_000
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_REFERENCE_COUNT = 128
_MAX_ARTIFACT_COUNT = 64
_MAX_COORDINATE = 1.0e12
_ATTRIBUTE_COMPONENTS = {"colors": 4, "intensities": 1, "normals": 3}
_REFERENCE_POINTS: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})
_APPROVED_ARTIFACTS: Mapping[str, Mapping[str, Any]] = MappingProxyType({})


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded model repair for every Points failure stage."""

    stage = str(details.get("stage") or "")
    path = str(details.get("path") or "")
    output_name = str(details.get("output_name") or "")
    location = f" at {path}" if path else (f" {output_name!r}" if output_name else "")
    if stage == "result_contract":
        return (
            "Return exactly the declared expected_outputs names, type='points', and order. "
            "Replace only the mismatched result value; keep the declarations unchanged."
        )
    if stage == "definition_contract":
        return (
            f"Rebuild only the malformed value{location} with api.point_cloud; never "
            "construct, copy, or mutate a serialized point-cloud definition."
        )
    if stage == "source_selection":
        return (
            "Copy one exact reference from document_point_clouds or one available artifact_id "
            "from approved_point_artifacts; do not use a label, path, stale id, or guessed name."
        )
    if stage == "source_import":
        return (
            "Keep the intended source identity, inspect its current availability/format in "
            "domain context, and replace only the unavailable or unreadable source before retrying."
        )
    if stage == "source_validation":
        return (
            "Repair only the reported source coordinate/attribute/structure defect, or set "
            "invalid_points='drop' explicitly when losing those points is acceptable."
        )
    if stage == "pipeline_transform":
        return (
            "Change only the reported transform stage so every transformed coordinate and "
            "normal is finite and bounded; keep the remaining ordered pipeline unchanged."
        )
    if stage == "pipeline_filter":
        return (
            "Change only the reported filter stage. For more than 500000 points, crop or voxel-"
            "sample before exact deduplication; otherwise adjust only its bounds or tolerance."
        )
    if stage == "pipeline_sample":
        return (
            "Change only the reported sample stage's voxel size, stride/offset, or point limit "
            "and preserve the rest of the ordered pipeline."
        )
    if stage == "pipeline_empty":
        index = details.get("pipeline_index")
        stage_name = f"pipeline[{index}]" if type(index) is int else "the reported pipeline stage"
        return (
            f"Change only {stage_name} so it retains at least one point in the coordinate "
            "frame established by all preceding stages; keep later stages unchanged."
        )
    if stage == "artifact_export":
        return (
            "Keep the validated point source and pipeline unchanged, then retry only after the "
            "isolated worker can write and authenticate its bounded ASC/attribute artifacts."
        )
    return (
        "Correct only the reported Points source or ordered pipeline stage and retry the failed "
        "working revision; do not recreate the program."
    )


class PointsCandidateError(RuntimeError):
    """A model-correctable Points failure with structured diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.details = dict(details or {})
        if not str(self.details.get("correction") or "").strip():
            changes = self.details.get("required_changes")
            correction = (
                next(
                    (str(item).strip() for item in changes if str(item).strip()),
                    "",
                )
                if isinstance(changes, list)
                else ""
            )
            self.details["correction"] = correction or _default_correction(
                self.details
            )
        super().__init__(message)


def _fail(message: str, *, stage: str, **details: Any) -> PointsCandidateError:
    return PointsCandidateError(message, details={"stage": stage, **details})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_artifact_path(
    root: Path,
    relative: Any,
    *,
    context: str,
    stage: str = "source_validation",
) -> Path:
    resolved_root = Path(root).resolve()
    if not isinstance(relative, str) or not relative:
        raise _fail(
            f"{context} has no artifact path.",
            stage=stage,
            path=context,
        )
    path = (resolved_root / relative).resolve()
    if resolved_root not in path.parents or not path.is_file():
        raise _fail(
            f"{context} artifact is missing or outside staging.",
            stage=stage,
            path=context,
        )
    size = path.stat().st_size
    if not 1 <= size <= _MAX_ARTIFACT_BYTES:
        raise _fail(
            f"{context} artifact size must be 1-{_MAX_ARTIFACT_BYTES} bytes.",
            stage=stage,
            path=context,
            artifact_bytes=size,
        )
    return path


def _reference_key(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _fail(
            f"{context} must contain exactly document_uid and object_name.",
            stage="source_selection",
            path=context,
        )
    result = []
    for name in ("document_uid", "object_name"):
        raw = value.get(name)
        if (
            not isinstance(raw, str)
            or not raw
            or raw != raw.strip()
            or len(raw) > 256
            or "\0" in raw
        ):
            raise _fail(
                f"{context}.{name} must be a non-empty string of at most 256 "
                "characters without surrounding whitespace or nulls.",
                stage="source_selection",
                path=f"{context}.{name}",
            )
        result.append(raw)
    return result[0], result[1]


def _attribute_values(
    value: Any,
    *,
    name: str,
    count: int,
    context: str,
) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(
            f"{context}.{name} must contain exactly {count} per-point values."
        )
    components = _ATTRIBUTE_COMPONENTS[name]
    result = []
    for index, raw in enumerate(value):
        if components == 1:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{context}.{name}[{index}] must be numeric.")
            clean = float(raw)
            if not math.isfinite(clean):
                raise ValueError(f"{context}.{name}[{index}] must be finite.")
            result.append(clean)
            continue
        if not isinstance(raw, (list, tuple)) or len(raw) != components:
            raise ValueError(
                f"{context}.{name}[{index}] must contain {components} numbers."
            )
        clean_values = []
        for component, item in enumerate(raw):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(
                    f"{context}.{name}[{index}][{component}] must be numeric."
                )
            clean = float(item)
            if not math.isfinite(clean):
                raise ValueError(
                    f"{context}.{name}[{index}][{component}] must be finite."
                )
            if name == "colors" and not 0.0 <= clean <= 1.0:
                raise ValueError(
                    f"{context}.{name}[{index}][{component}] must be in [0, 1]."
                )
            clean_values.append(clean)
        result.append(tuple(clean_values))
    return result


def _write_f32(path: Path, values: Sequence[float]) -> None:
    payload = array("f", (float(value) for value in values))
    if sys.byteorder != "little":
        payload.byteswap()
    with path.open("wb") as handle:
        payload.tofile(handle)


def _read_f32(path: Path, count: int) -> array:
    values = array("f")
    try:
        with path.open("rb") as handle:
            values.fromfile(handle, count)
            if handle.read(1):
                raise ValueError(
                    f"Float artifact {path.name!r} contains trailing bytes."
                )
    except EOFError as exc:
        raise ValueError(
            f"Float artifact {path.name!r} ended before {count} values."
        ) from exc
    if len(values) != count:
        raise ValueError(
            f"Float artifact {path.name!r} contains {len(values)} values; expected {count}."
        )
    if sys.byteorder != "little":
        values.byteswap()
    return values


def write_point_attribute_artifacts(
    root: Path,
    prefix: str,
    attributes: Mapping[str, Sequence[Any]],
    *,
    point_count: int,
    relative_directory: str | Path = "outputs",
) -> list[dict[str, Any]]:
    """Serialize aligned point attributes as authenticated float32 sidecars."""

    result = []
    for name in ("colors", "intensities", "normals"):
        values = list(attributes.get(name) or [])
        if not values:
            continue
        clean = _attribute_values(
            values,
            name=name,
            count=point_count,
            context="point_attributes",
        )
        components = _ATTRIBUTE_COMPONENTS[name]
        flattened = (
            [float(value) for value in clean]
            if components == 1
            else [float(item) for value in clean for item in value]
        )
        relative = Path(relative_directory) / f"{prefix}-{name}.f32"
        path = Path(root) / relative
        _write_f32(path, flattened)
        expected_bytes = point_count * components * 4
        if path.stat().st_size != expected_bytes:
            raise _fail(
                f"Point attribute {name!r} serialized to an unexpected size.",
                stage="artifact_export",
                attribute=name,
                expected_bytes=expected_bytes,
                observed_bytes=path.stat().st_size,
            )
        result.append(
            {
                "schema": ATTRIBUTE_SCHEMA,
                "name": name,
                "components": components,
                "count": point_count,
                "artifact_path": str(relative),
                "artifact_bytes": expected_bytes,
                "artifact_sha256": _sha256_file(path),
            }
        )
    return result


def load_point_attribute_artifacts(
    root: Path,
    descriptors: Any,
    *,
    point_count: int,
    context: str,
) -> dict[str, list[Any]]:
    """Authenticate and deserialize float32 sidecars."""

    if not isinstance(descriptors, list) or len(descriptors) > len(
        _ATTRIBUTE_COMPONENTS
    ):
        raise ValueError(f"{context} must be a bounded attribute artifact array.")
    result: dict[str, list[Any]] = {}
    for index, descriptor in enumerate(descriptors):
        item_context = f"{context}[{index}]"
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "schema",
            "name",
            "components",
            "count",
            "artifact_path",
            "artifact_bytes",
            "artifact_sha256",
        }:
            raise ValueError(f"{item_context} has malformed fields.")
        if descriptor["schema"] != ATTRIBUTE_SCHEMA:
            raise ValueError(f"{item_context} has an unsupported attribute schema.")
        name = descriptor["name"]
        if name not in _ATTRIBUTE_COMPONENTS or name in result:
            raise ValueError(f"{item_context}.name is unsupported or duplicated.")
        components = _ATTRIBUTE_COMPONENTS[name]
        expected_bytes = point_count * components * 4
        if (
            descriptor["components"] != components
            or descriptor["count"] != point_count
            or descriptor["artifact_bytes"] != expected_bytes
        ):
            raise ValueError(f"{item_context} does not match the point count or type.")
        path = _bounded_artifact_path(
            Path(root), descriptor["artifact_path"], context=item_context
        )
        if path.stat().st_size != expected_bytes:
            raise ValueError(f"{item_context} changed size during transfer.")
        if _sha256_file(path) != descriptor["artifact_sha256"]:
            raise ValueError(f"{item_context} SHA-256 does not match its descriptor.")
        values = _read_f32(path, point_count * components)
        if components == 1:
            clean: list[Any] = [float(value) for value in values]
        else:
            clean = [
                tuple(float(values[offset + component]) for component in range(components))
                for offset in range(0, len(values), components)
            ]
        result[name] = _attribute_values(
            clean,
            name=name,
            count=point_count,
            context=item_context,
        )
    return result


def _structured(value: Any, *, point_count: int, context: str) -> dict[str, int] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, Mapping) or set(value) != {"width", "height"}:
        raise ValueError(f"{context} must contain exactly width and height.")
    width = value.get("width")
    height = value.get("height")
    if (
        isinstance(width, bool)
        or type(width) is not int
        or isinstance(height, bool)
        or type(height) is not int
        or width < 1
        or height < 1
        or width * height != point_count
    ):
        raise ValueError(
            f"{context} dimensions must be positive and multiply to {point_count}."
        )
    return {"width": width, "height": height}


def configure_points_sources(
    root: Path,
    document_references: list[dict[str, Any]],
    approved_artifacts: list[dict[str, Any]],
) -> None:
    """Authenticate host-staged document clouds and approved project artifacts."""

    import Points

    if len(document_references) > _MAX_REFERENCE_COUNT:
        raise _fail(
            f"Points accepts at most {_MAX_REFERENCE_COUNT} document references.",
            stage="source_selection",
            reference_count=len(document_references),
        )
    references: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(document_references):
        context = f"document_references[{index}]"
        if not isinstance(entry, dict):
            raise _fail(
                f"{context} must be an object.",
                stage="source_selection",
                path=context,
            )
        key = _reference_key(
            {
                "document_uid": entry.get("document_uid"),
                "object_name": entry.get("object_name"),
            },
            context=context,
        )
        if key in references or entry.get("artifact_kind") != "points_asc":
            raise _fail(
                f"{context} has duplicate identity or wrong artifact kind.",
                stage="source_selection",
                path=context,
                reference={"document_uid": key[0], "object_name": key[1]},
            )
        path = _bounded_artifact_path(
            Path(root),
            entry.get("artifact_path"),
            context=context,
            stage="source_selection",
        )
        expected_digest = entry.get("artifact_sha256")
        if not isinstance(expected_digest, str) or _sha256_file(path) != expected_digest:
            raise _fail(
                f"{context} coordinate SHA-256 does not match.",
                stage="source_selection",
                path=context,
                reference={"document_uid": key[0], "object_name": key[1]},
            )
        try:
            kernel = Points.Points(str(path))
        except Exception as exc:
            raise _fail(
                f"Could not load staged point reference {key[1]!r}: "
                f"{type(exc).__name__}: {exc}",
                stage="source_import",
                path=context,
                reference={"document_uid": key[0], "object_name": key[1]},
                exception_type=type(exc).__name__,
            ) from exc
        count = int(kernel.CountPoints)
        if not 1 <= count <= _MAX_POINTS:
            raise _fail(
                f"{context} must contain 1-{_MAX_POINTS} points.",
                stage="source_import",
                path=context,
                point_count=count,
            )
        try:
            attributes = load_point_attribute_artifacts(
                Path(root),
                entry.get("attribute_artifacts", []),
                point_count=count,
                context=f"{context}.attribute_artifacts",
            )
            structured = _structured(
                entry.get("structured"),
                point_count=count,
                context=f"{context}.structured",
            )
        except PointsCandidateError:
            raise
        except ValueError as exc:
            raise _fail(
                str(exc),
                stage="source_validation",
                path=context,
                reference={"document_uid": key[0], "object_name": key[1]},
            ) from exc
        metadata = {
            key_name: entry.get(key_name)
            for key_name in (
                "document_uid",
                "object_name",
                "label",
                "type_id",
                "artifact_kind",
                "artifact_sha256",
                "source_kind",
                "source_program_id",
                "source_program_domain",
                "source_revision",
                "transient_topology",
            )
            if entry.get(key_name) not in (None, "")
        }
        references[key] = MappingProxyType(
            {
                "kernel": kernel,
                "attributes": attributes,
                "structured": structured,
                "metadata": metadata,
            }
        )
    if len(approved_artifacts) > _MAX_ARTIFACT_COUNT:
        raise _fail(
            f"Points accepts at most {_MAX_ARTIFACT_COUNT} approved artifacts.",
            stage="source_selection",
            artifact_count=len(approved_artifacts),
        )
    artifacts: dict[str, Mapping[str, Any]] = {}
    for index, entry in enumerate(approved_artifacts):
        context = f"point_artifacts[{index}]"
        if not isinstance(entry, dict):
            raise _fail(
                f"{context} must be an object.",
                stage="source_selection",
                path=context,
            )
        artifact_id = entry.get("artifact_id")
        if (
            not isinstance(artifact_id, str)
            or len(artifact_id) != 32
            or any(character not in "0123456789abcdef" for character in artifact_id)
        ):
            raise _fail(
                f"{context} has an invalid artifact id.",
                stage="source_selection",
                path=f"{context}.artifact_id",
            )
        if artifact_id in artifacts:
            raise _fail(
                f"{context} duplicates an artifact id.",
                stage="source_selection",
                path=f"{context}.artifact_id",
                artifact_id=artifact_id,
            )
        path = _bounded_artifact_path(
            Path(root),
            entry.get("artifact_path"),
            context=context,
            stage="source_selection",
        )
        digest = entry.get("artifact_sha256")
        if not isinstance(digest, str) or _sha256_file(path) != digest:
            raise _fail(
                f"{context} SHA-256 does not match.",
                stage="source_selection",
                path=context,
                artifact_id=artifact_id,
            )
        worker_format = entry.get("worker_format")
        if worker_format not in {"asc", "e57", "pcd", "ply"}:
            raise _fail(
                f"{context} has an unsupported format.",
                stage="source_import",
                path=f"{context}.worker_format",
                artifact_id=artifact_id,
                format=worker_format,
            )
        artifacts[artifact_id] = MappingProxyType(
            {
                "artifact_id": artifact_id,
                "name": str(entry.get("name") or ""),
                "label": str(entry.get("label") or ""),
                "format": str(entry.get("format") or ""),
                "worker_format": worker_format,
                "artifact_sha256": digest,
                "artifact_bytes": int(path.stat().st_size),
                "path": path,
            }
        )
    global _REFERENCE_POINTS, _APPROVED_ARTIFACTS
    _REFERENCE_POINTS = MappingProxyType(references)
    _APPROVED_ARTIFACTS = MappingProxyType(artifacts)


def _encoded(value: Any) -> bytes:
    try:
        result = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"A Points definition is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc
    if len(result) > _MAX_DEFINITION_BYTES:
        raise _fail(
            f"A Points definition exceeds {_MAX_DEFINITION_BYTES} JSON bytes.",
            stage="definition_contract",
            json_bytes=len(result),
        )
    return result


def _canonical_source(payload: Mapping[str, Any], *, context: str) -> Any:
    if set(payload) != {"kind", "reference", "artifact_id", "points"}:
        raise _fail(
            f"{context} has malformed source fields.",
            stage="definition_contract",
            path=context,
        )
    kind = payload.get("kind")
    if kind == "document":
        return payload.get("reference")
    if kind == "artifact":
        return {"artifact_id": payload.get("artifact_id")}
    if kind == "inline":
        return payload.get("points")
    raise _fail(
        f"{context}.kind is unsupported.",
        stage="definition_contract",
        path=f"{context}.kind",
    )


def _provider_pipeline(value: Any, *, context: str) -> list[dict[str, Any]]:
    """Reconstruct exact public stage arguments from the normalized graph form."""

    if not isinstance(value, list):
        raise _fail(
            f"{context} must be an array.",
            stage="definition_contract",
            path=context,
        )
    result = []
    fields_by_stage = {
        ("transform", None): ("translation", "rotation", "scale"),
        ("filter", "crop_box"): ("minimum", "maximum"),
        ("filter", "deduplicate"): ("tolerance",),
        ("sample", "voxel"): ("voxel_size", "reduction"),
        ("sample", "stride"): ("step", "offset"),
        ("sample", "limit"): ("max_points",),
    }
    normalized_fields = {
        "op",
        "method",
        "translation",
        "rotation",
        "scale",
        "minimum",
        "maximum",
        "tolerance",
        "voxel_size",
        "reduction",
        "step",
        "offset",
        "max_points",
    }
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != normalized_fields:
            raise _fail(
                f"{context}[{index}] has malformed normalized fields.",
                stage="definition_contract",
                path=f"{context}[{index}]",
            )
        operation = raw.get("op")
        method = raw.get("method")
        fields = fields_by_stage.get((operation, method))
        if fields is None:
            raise _fail(
                f"{context}[{index}] has an unsupported operation or method.",
                stage="definition_contract",
                path=f"{context}[{index}]",
            )
        stage = {"op": operation}
        if method is not None:
            stage["method"] = method
        for field in fields:
            if raw.get(field) is not None:
                stage[field] = raw[field]
        result.append(stage)
    return result


def validate_points_definition(
    value: Any,
    *,
    require_domain_value: bool = True,
    context: str = "result",
) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping) and not require_domain_value:
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be a value returned by api.point_cloud.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields:
        raise _fail(
            f"{context} has malformed Points definition fields.",
            stage="definition_contract",
            path=context,
        )
    if (
        payload.get("domain") != "points"
        or payload.get("operation") != "point_cloud"
        or payload.get("output_type") != "points"
    ):
        raise _fail(
            f"{context} is not a supported Points graph value.",
            stage="definition_contract",
            path=context,
        )
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if not isinstance(arguments, list) or len(arguments) != 1:
        raise _fail(
            f"{context} must contain exactly one source argument.",
            stage="definition_contract",
            path=f"{context}.arguments",
        )
    if not isinstance(arguments[0], Mapping) or not isinstance(properties, dict):
        raise _fail(
            f"{context} has malformed source or properties.",
            stage="definition_contract",
            path=context,
        )
    _encoded(payload)
    try:
        source = _canonical_source(arguments[0], context=f"{context}.arguments[0]")
        public_properties = dict(properties)
        public_properties["pipeline"] = _provider_pipeline(
            properties.get("pipeline"),
            context=f"{context}.properties.pipeline",
        )
        rebuilt = PointsDomainAPI(_EXPORTS, _OUTPUT_TYPES).point_cloud(
            source,
            **public_properties,
        ).to_payload()
    except (TypeError, ValueError) as exc:
        raise _fail(
            str(exc),
            stage="definition_contract",
            path=context,
            exception_type=type(exc).__name__,
        ) from exc
    if rebuilt != payload:
        raise _fail(
            f"{context} is not the canonical result of api.point_cloud.",
            stage="definition_contract",
            path=context,
            required_changes=[
                "Recreate this value with api.point_cloud; do not edit serialized fields."
            ],
        )
    return payload


def _extract_feature_attributes(obj: Any, count: int) -> dict[str, list[Any]]:
    values = {}
    for property_name, name in (
        ("Color", "colors"),
        ("Intensity", "intensities"),
        ("Normal", "normals"),
    ):
        if not hasattr(obj, property_name):
            continue
        raw = list(getattr(obj, property_name) or [])
        if raw:
            if name in {"colors", "normals"}:
                raw = [
                    (
                        tuple(float(component) for component in item)
                        if name == "colors"
                        else (float(item.x), float(item.y), float(item.z))
                    )
                    for item in raw
                ]
            values[name] = _attribute_values(
                raw,
                name=name,
                count=count,
                context=f"source.{property_name}",
            )
    return values


def _rotate_vector(
    value: tuple[float, float, float],
    quaternion: Sequence[float],
) -> tuple[float, float, float]:
    x, y, z = value
    qx, qy, qz, qw = (float(item) for item in quaternion)
    tx = 2.0 * (qy * z - qz * y)
    ty = 2.0 * (qz * x - qx * z)
    tz = 2.0 * (qx * y - qy * x)
    return (
        x + qw * tx + (qy * tz - qz * ty),
        y + qw * ty + (qz * tx - qx * tz),
        z + qw * tz + (qx * ty - qy * tx),
    )


def _placement_quaternion(placement: Any) -> tuple[float, float, float, float]:
    return tuple(float(value) for value in placement.Rotation.Q)


def _state_from_kernel(
    kernel: Any,
    *,
    attributes: Mapping[str, Sequence[Any]],
    structured: Mapping[str, int] | None,
    invalid_points: str,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    count = int(kernel.CountPoints)
    if not 1 <= count <= _MAX_POINTS:
        raise _fail(
            f"Point source contains {count} points; accepted range is 1-{_MAX_POINTS}.",
            stage="source_import",
            point_count=count,
        )
    clean_attributes = {
        name: _attribute_values(
            values,
            name=name,
            count=count,
            context="source.attributes",
        )
        for name, values in attributes.items()
        if values
    }
    placement = kernel.Placement
    quaternion = _placement_quaternion(placement)
    points = []
    retained = []
    invalid_indices = []
    for index, raw in enumerate(list(kernel.Points)):
        # PointKernel iteration already applies its ComplexGeoData transform. Applying
        # Placement again here would double-transform document and E57 sources while
        # their context bounds and samples correctly remain in world coordinates.
        point = (float(raw.x), float(raw.y), float(raw.z))
        if not all(
            math.isfinite(value) and abs(value) <= _MAX_COORDINATE for value in point
        ):
            invalid_indices.append(index + 1)
            continue
        retained.append(index)
        points.append(point)
    if invalid_indices and invalid_points == "reject":
        raise _fail(
            f"Point source contains {len(invalid_indices)} invalid or out-of-range points.",
            stage="source_validation",
            invalid_point_indices=invalid_indices[:64],
            invalid_point_indices_truncated=len(invalid_indices) > 64,
            required_changes=[
                "Repair the source or use invalid_points='drop' explicitly."
            ],
        )
    if not points:
        raise _fail(
            "Point source contains no valid points after invalid-value handling.",
            stage="source_validation",
        )
    if invalid_indices:
        clean_attributes = {
            name: [values[index] for index in retained]
            for name, values in clean_attributes.items()
        }
        structured = None
    normals = clean_attributes.get("normals")
    if normals:
        clean_normals = []
        for index, normal in enumerate(normals):
            rotated = _rotate_vector(tuple(normal), quaternion)
            length = math.sqrt(sum(value * value for value in rotated))
            if not math.isfinite(length) or length <= 1.0e-20:
                raise _fail(
                    f"Source normal {index + 1} has zero or invalid length.",
                    stage="source_validation",
                    normal_index=index + 1,
                )
            clean_normals.append(tuple(value / length for value in rotated))
        clean_attributes["normals"] = clean_normals
    return {
        "points": points,
        "attributes": clean_attributes,
        "structured": dict(structured) if structured is not None else None,
        "source": dict(source),
        "invalid_points_removed": len(invalid_indices),
    }


def bake_point_reference(
    kernel: Any,
    attributes: Mapping[str, Sequence[Any]],
    structured: Mapping[str, int] | None,
) -> tuple[Any, dict[str, list[Any]], dict[str, int] | None]:
    """Bake a detached document placement into coordinates and normals."""

    import FreeCAD as App
    import Points

    state = _state_from_kernel(
        kernel,
        attributes=attributes,
        structured=structured,
        invalid_points="reject",
        source={"kind": "document_staging"},
    )
    baked = Points.Points([App.Vector(*point) for point in state["points"]])
    baked.Placement = App.Placement()
    return baked, dict(state["attributes"]), state["structured"]


def _load_source(
    source: Mapping[str, Any],
    properties: Mapping[str, Any],
    document: Any,
) -> dict[str, Any]:
    import FreeCAD as App
    import Points

    kind = str(source["kind"])
    invalid_points = str(properties["invalid_points"])
    if kind == "inline":
        points = list(source["points"] or [])
        kernel = Points.Points([App.Vector(*point) for point in points])
        return _state_from_kernel(
            kernel,
            attributes={},
            structured=None,
            invalid_points=invalid_points,
            source={"kind": "inline", "point_count": len(points)},
        )
    if kind == "document":
        key = _reference_key(source["reference"], context="point source")
        entry = _REFERENCE_POINTS.get(key)
        if entry is None:
            raise _fail(
                f"Document object {key[1]!r} is not an authenticated Points source.",
                stage="source_selection",
                reference={"document_uid": key[0], "object_name": key[1]},
                required_changes=[
                    "Reference a live Points::Feature exposed in the Points domain context."
                ],
            )
        return _state_from_kernel(
            entry["kernel"].copy(),
            attributes=entry["attributes"],
            structured=entry["structured"],
            invalid_points=invalid_points,
            source={"kind": "document", **dict(entry["metadata"])},
        )
    artifact_id = str(source.get("artifact_id") or "")
    artifact = _APPROVED_ARTIFACTS.get(artifact_id)
    if artifact is None:
        raise _fail(
            f"Project point artifact {artifact_id!r} is not authenticated for this candidate.",
            stage="source_selection",
            artifact_id=artifact_id,
            required_changes=[
                "Use an available artifact_id from the Points domain context."
            ],
        )
    before = {str(obj.Name) for obj in list(document.Objects)}
    try:
        Points.insert(str(artifact["path"]), str(document.Name))
    except Exception as exc:
        raise _fail(
            f"Native Points import failed for artifact {artifact_id!r}: "
            f"{type(exc).__name__}: {exc}",
            stage="source_import",
            artifact_id=artifact_id,
            format=artifact["format"],
            exception_type=type(exc).__name__,
        ) from exc
    imported = [
        obj
        for obj in list(document.Objects)
        if str(obj.Name) not in before
        and (
            str(getattr(obj, "TypeId", "")).startswith("Points::")
            or bool(obj.isDerivedFrom("Points::Feature"))
        )
    ]
    if len(imported) != 1:
        raise _fail(
            f"Native Points import created {len(imported)} point objects; exactly one is required.",
            stage="source_import",
            artifact_id=artifact_id,
            imported_objects=[str(obj.Name) for obj in imported],
        )
    obj = imported[0]
    kernel = obj.Points.copy()
    count = int(kernel.CountPoints)
    attributes = _extract_feature_attributes(obj, count)
    width = int(getattr(obj, "Width", 0) or 0)
    height = int(getattr(obj, "Height", 0) or 0)
    structured = (
        {"width": width, "height": height}
        if width > 0 and height > 0 and width * height == count
        else None
    )
    return _state_from_kernel(
        kernel,
        attributes=attributes,
        structured=structured,
        invalid_points=invalid_points,
        source={
            key: artifact[key]
            for key in (
                "artifact_id",
                "name",
                "label",
                "format",
                "artifact_sha256",
                "artifact_bytes",
            )
        }
        | {"kind": "artifact"},
    )


def _select_state(state: dict[str, Any], indices: Sequence[int]) -> None:
    original_count = len(state["points"])
    selected = list(indices)
    state["points"] = [state["points"][index] for index in selected]
    state["attributes"] = {
        name: [values[index] for index in selected]
        for name, values in state["attributes"].items()
    }
    if selected != list(range(original_count)):
        state["structured"] = None


def _transform(state: dict[str, Any], stage: Mapping[str, Any]) -> None:
    translation = tuple(float(value) for value in stage["translation"])
    rotation = tuple(float(value) for value in stage["rotation"])
    scale = tuple(float(value) for value in stage["scale"])
    transformed = []
    for index, point in enumerate(state["points"]):
        scaled = tuple(point[axis] * scale[axis] for axis in range(3))
        rotated = _rotate_vector(scaled, rotation)
        result = tuple(rotated[axis] + translation[axis] for axis in range(3))
        if not all(
            math.isfinite(value) and abs(value) <= _MAX_COORDINATE for value in result
        ):
            raise _fail(
                f"Transform produced an invalid or out-of-range point at index {index + 1}.",
                stage="pipeline_transform",
                point_index=index + 1,
            )
        transformed.append(result)
    state["points"] = transformed
    normals = state["attributes"].get("normals")
    if normals:
        transformed_normals = []
        for index, normal in enumerate(normals):
            adjusted = tuple(normal[axis] / scale[axis] for axis in range(3))
            rotated = _rotate_vector(adjusted, rotation)
            length = math.sqrt(sum(value * value for value in rotated))
            if not math.isfinite(length) or length <= 1.0e-20:
                raise _fail(
                    f"Transform produced an invalid normal at index {index + 1}.",
                    stage="pipeline_transform",
                    normal_index=index + 1,
                )
            transformed_normals.append(tuple(value / length for value in rotated))
        state["attributes"]["normals"] = transformed_normals


def _crop_box(state: dict[str, Any], stage: Mapping[str, Any]) -> None:
    minimum = tuple(float(value) for value in stage["minimum"])
    maximum = tuple(float(value) for value in stage["maximum"])
    _select_state(
        state,
        [
            index
            for index, point in enumerate(state["points"])
            if all(minimum[axis] <= point[axis] <= maximum[axis] for axis in range(3))
        ],
    )


def _deduplicate(state: dict[str, Any], stage: Mapping[str, Any]) -> None:
    points = state["points"]
    if len(points) > _MAX_DEDUPLICATE_POINTS:
        raise _fail(
            f"Exact tolerance deduplication is bounded to {_MAX_DEDUPLICATE_POINTS} "
            f"points; received {len(points)}.",
            stage="pipeline_filter",
            method="deduplicate",
            point_count=len(points),
            required_changes=[
                "Use sample method='voxel' for larger clouds, or downsample before deduplication."
            ],
        )
    tolerance = float(stage["tolerance"])
    tolerance_squared = tolerance * tolerance
    cells: dict[tuple[int, int, int], list[int]] = {}
    retained = []
    for index, point in enumerate(points):
        cell = tuple(math.floor(value / tolerance) for value in point)
        duplicate = False
        for dx in (-1, 0, 1):
            if duplicate:
                break
            for dy in (-1, 0, 1):
                if duplicate:
                    break
                for dz in (-1, 0, 1):
                    for prior_index in cells.get(
                        (cell[0] + dx, cell[1] + dy, cell[2] + dz), []
                    ):
                        prior = points[prior_index]
                        if (
                            sum(
                                (point[axis] - prior[axis]) ** 2
                                for axis in range(3)
                            )
                            <= tolerance_squared
                        ):
                            duplicate = True
                            break
                    if duplicate:
                        break
        if not duplicate:
            retained.append(index)
            cells.setdefault(cell, []).append(index)
    _select_state(state, retained)


def _voxel_sample(state: dict[str, Any], stage: Mapping[str, Any]) -> None:
    size = float(stage["voxel_size"])
    reduction = str(stage["reduction"])
    points = state["points"]
    groups: dict[tuple[int, int, int], list[int]] = {}
    for index, point in enumerate(points):
        cell = tuple(math.floor(value / size) for value in point)
        groups.setdefault(cell, []).append(index)
    if reduction == "first":
        _select_state(state, [indices[0] for indices in groups.values()])
        return
    attributes = state["attributes"]
    output_points = []
    output_attributes = {name: [] for name in attributes}
    for indices in groups.values():
        count = len(indices)
        output_points.append(
            tuple(
                math.fsum(points[index][axis] for index in indices) / count
                for axis in range(3)
            )
        )
        for name, values in attributes.items():
            components = _ATTRIBUTE_COMPONENTS[name]
            if components == 1:
                output_attributes[name].append(
                    math.fsum(float(values[index]) for index in indices) / count
                )
            else:
                averaged = tuple(
                    math.fsum(float(values[index][component]) for index in indices)
                    / count
                    for component in range(components)
                )
                if name == "normals":
                    length = math.sqrt(sum(value * value for value in averaged))
                    if length <= 1.0e-20:
                        averaged = tuple(float(values[indices[0]][axis]) for axis in range(3))
                    else:
                        averaged = tuple(value / length for value in averaged)
                output_attributes[name].append(averaged)
    state["points"] = output_points
    state["attributes"] = output_attributes
    state["structured"] = None


def _sample(state: dict[str, Any], stage: Mapping[str, Any]) -> None:
    method = str(stage["method"])
    if method == "voxel":
        _voxel_sample(state, stage)
        return
    count = len(state["points"])
    if method == "stride":
        indices = list(range(int(stage["offset"]), count, int(stage["step"])))
    else:
        maximum = int(stage["max_points"])
        if count <= maximum:
            indices = list(range(count))
        elif maximum == 1:
            indices = [0]
        else:
            indices = [
                round(index * (count - 1) / (maximum - 1))
                for index in range(maximum)
            ]
    _select_state(state, indices)


def _apply_pipeline(
    state: dict[str, Any],
    pipeline: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    trace = []
    for index, stage in enumerate(pipeline):
        before = len(state["points"])
        operation = str(stage["op"])
        method = str(stage.get("method") or "")
        if operation == "transform":
            _transform(state, stage)
        elif operation == "filter" and method == "crop_box":
            _crop_box(state, stage)
        elif operation == "filter":
            _deduplicate(state, stage)
        else:
            _sample(state, stage)
        after = len(state["points"])
        if after <= 0:
            raise _fail(
                f"Points pipeline stage {index + 1} removed every point.",
                stage="pipeline_empty",
                pipeline_index=index,
                operation=operation,
                method=method or None,
                input_count=before,
                required_changes=[
                    f"Change only pipeline[{index}] so it retains at least one of its "
                    f"{before} input points in the coordinate frame established by preceding "
                    "stages."
                ],
            )
        trace.append(
            {
                "index": index,
                "op": operation,
                "method": method or None,
                "input_count": before,
                "output_count": after,
                "removed_count": before - after,
            }
        )
    return trace


def point_facts(
    kernel: Any,
    *,
    attributes: Mapping[str, Sequence[Any]] | None = None,
    structured: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return bounded native point facts shared by worker and host validator."""

    import FreeCAD as App

    local = kernel.copy()
    local.Placement = App.Placement()
    count = int(local.CountPoints)
    if not 1 <= count <= _MAX_POINTS:
        raise ValueError(f"Point kernel must contain 1-{_MAX_POINTS} points.")
    points = [
        (float(point.x), float(point.y), float(point.z))
        for point in list(local.Points)
    ]
    for index, point in enumerate(points):
        if not all(
            math.isfinite(value) and abs(value) <= _MAX_COORDINATE for value in point
        ):
            raise ValueError(f"Point kernel coordinate {index + 1} is invalid.")
    box = local.BoundBox
    clean_attributes = {
        name: _attribute_values(
            values,
            name=name,
            count=count,
            context="point_facts.attributes",
        )
        for name, values in dict(attributes or {}).items()
        if values
    }
    clean_structured = _structured(
        structured,
        point_count=count,
        context="point_facts.structured",
    )
    sample_indices = list(range(min(4, count)))
    sample_indices.extend(
        index
        for index in range(max(0, count - 4), count)
        if index not in sample_indices
    )
    return {
        "points": count,
        "bounds": {
            "minimum": [float(box.XMin), float(box.YMin), float(box.ZMin)],
            "maximum": [float(box.XMax), float(box.YMax), float(box.ZMax)],
            "size": [float(box.XLength), float(box.YLength), float(box.ZLength)],
        },
        "centroid": [
            math.fsum(point[axis] for point in points) / count for axis in range(3)
        ],
        "sample": [list(points[index]) for index in sample_indices],
        "sample_indices": [index + 1 for index in sample_indices],
        "attributes": {
            name: {"count": count, "components": _ATTRIBUTE_COMPONENTS[name]}
            for name in sorted(clean_attributes)
        },
        "structured": clean_structured,
    }


def _export_points(kernel: Any, path: Path, *, output_name: str) -> str:
    try:
        kernel.write(str(path))
    except Exception as exc:
        raise _fail(
            f"Could not export Points output {output_name!r}: "
            f"{type(exc).__name__}: {exc}",
            stage="artifact_export",
            output_name=output_name,
            exception_type=type(exc).__name__,
        ) from exc
    if (
        not path.is_file()
        or path.stat().st_size <= 0
        or path.stat().st_size > _MAX_ARTIFACT_BYTES
    ):
        raise _fail(
            f"Points output {output_name!r} has an invalid coordinate artifact size.",
            stage="artifact_export",
            output_name=output_name,
            artifact_bytes=path.stat().st_size if path.is_file() else 0,
        )
    return _sha256_file(path)


def validate_and_process_points(
    result: Mapping[str, Any],
    expected_outputs: Sequence[Mapping[str, Any]],
    root: Path,
    document: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate, execute, diagnose, and serialize all point-cloud outputs."""

    import FreeCAD as App
    import Points

    expected_names = [str(item.get("name") or "") for item in expected_outputs]
    if list(result) != expected_names:
        raise _fail(
            "Points result keys must exactly match expected_outputs in declared order.",
            stage="result_contract",
            expected=expected_names,
            received=list(result),
        )
    outputs = []
    summaries = []
    total_input_points = 0
    total_output_points = 0
    for index, declaration in enumerate(expected_outputs):
        name = str(declaration.get("name") or "")
        if declaration.get("type") != "points":
            raise _fail(
                f"Points output {name!r} must declare type 'points'.",
                stage="result_contract",
                output_name=name,
            )
        definition = validate_points_definition(
            result[name],
            context=f"result[{name!r}]",
        )
        source = dict(definition["arguments"][0])
        properties = dict(definition["properties"])
        state = _load_source(source, properties, document)
        input_count = len(state["points"])
        if not bool(properties["preserve_attributes"]):
            state["attributes"] = {}
        trace = _apply_pipeline(state, list(properties["pipeline"]))
        kernel = Points.Points([App.Vector(*point) for point in state["points"]])
        kernel.Placement = App.Placement()
        relative = Path("outputs") / f"output-{index:03d}.asc"
        digest = _export_points(kernel, Path(root) / relative, output_name=name)
        reloaded = Points.Points(str(Path(root) / relative))
        attribute_artifacts = write_point_attribute_artifacts(
            Path(root),
            f"output-{index:03d}",
            state["attributes"],
            point_count=int(reloaded.CountPoints),
        )
        aggregate_artifact_bytes = int((Path(root) / relative).stat().st_size) + sum(
            int(descriptor["artifact_bytes"])
            for descriptor in attribute_artifacts
        )
        if aggregate_artifact_bytes > _MAX_ARTIFACT_BYTES:
            raise _fail(
                "Points coordinate and attribute artifacts exceed the aggregate "
                f"{_MAX_ARTIFACT_BYTES}-byte output limit.",
                stage="artifact_export",
                output_name=name,
                aggregate_artifact_bytes=aggregate_artifact_bytes,
            )
        reloaded_attributes = load_point_attribute_artifacts(
            Path(root),
            attribute_artifacts,
            point_count=int(reloaded.CountPoints),
            context=f"output[{name!r}].attribute_artifacts",
        )
        facts = point_facts(
            reloaded,
            attributes=reloaded_attributes,
            structured=state["structured"],
        )
        data = {
            "schema": VALIDATION_SCHEMA,
            "operation": "point_cloud",
            "label": str(properties["label"]),
            "source": dict(state["source"]),
            "input_point_count": input_count,
            "output_point_count": int(facts["points"]),
            "invalid_points_policy": str(properties["invalid_points"]),
            "invalid_points_removed": int(state["invalid_points_removed"]),
            "preserve_attributes": bool(properties["preserve_attributes"]),
            "pipeline": list(properties["pipeline"]),
            "operation_trace": trace,
            "artifact_sha256": digest,
            "attribute_artifacts": attribute_artifacts,
            "facts": facts,
        }
        _encoded(data)
        outputs.append(
            {
                "name": name,
                "type": "points",
                "definition": definition,
                "artifact_kind": "points_asc",
                "artifact_path": str(relative),
                "artifact_sha256": digest,
                "attribute_artifacts": attribute_artifacts,
                "facts": facts,
                "points_data": data,
            }
        )
        summaries.append(
            {
                "name": name,
                "source_kind": str(state["source"].get("kind") or ""),
                "input_points": input_count,
                "output_points": int(facts["points"]),
                "attributes": sorted(facts["attributes"]),
                "artifact_sha256": digest,
            }
        )
        total_input_points += input_count
        total_output_points += int(facts["points"])
    return outputs, {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "total_input_points": total_input_points,
        "total_output_points": total_output_points,
        "outputs": summaries,
    }
