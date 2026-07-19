# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native worker for production Inspection VibeScript programs."""

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
from vibescript_inspection_api import InspectionDomainAPI


VALIDATION_SCHEMA = "vibecad-vibescript-inspection-validation-v1"
DISTANCE_SCHEMA = "vibecad-vibescript-inspection-distances-f32le-v1"
_EXPORTS = ("comparison", "group", "measurement", "report")
_OUTPUT_TYPES = (
    "inspection_group",
    "inspection_feature",
    "measurement",
    "report",
)
_OPERATION_OUTPUT = {
    "comparison": "inspection_feature",
    "group": "inspection_group",
    "measurement": "measurement",
    "report": "report",
}
_MAX_DEFINITION_BYTES = 1_000_000
_MAX_REFERENCES = 128
_MAX_DISTANCE_COUNT = 2_000_000
_MAX_ARTIFACT_BYTES = _MAX_DISTANCE_COUNT * 4
_FLOAT32_MAX = 3.4028234663852886e38
_UNMEASURED_THRESHOLD = _FLOAT32_MAX * 0.99
_REFERENCE_OPTIONAL_FIELDS = frozenset(
    {
        "label",
        "type_id",
        "shape_type",
        "facts",
        "attribute_artifacts",
        "structured",
        "mesh_segments",
        "mesh_source_placement_matrix",
        "source_kind",
        "source_program_id",
        "source_program_domain",
        "source_revision",
        "transient_topology",
        "requires_semantic_interfaces",
        "published_interfaces",
    }
)
_REFERENCES: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded model repair for every Inspection failure stage."""

    stage = str(details.get("stage") or "")
    path = str(details.get("path") or "")
    output = str(details.get("output") or "")
    location = f" at {path}" if path else (f" {output!r}" if output else "")
    if stage == "result_contract":
        return (
            "Return exactly the declared expected_outputs names, types, and order. Replace "
            "only the mismatched result entry and keep every declaration unchanged."
        )
    if stage == "definition_contract":
        return (
            f"Rebuild only the malformed value{location} with api.comparison, api.group, "
            "api.measurement, or api.report; never construct or mutate serialized definitions."
        )
    if stage == "source_validation":
        return (
            "Change only the named api argument: copy exact references, keep tolerance bounds "
            "inside search_radius, and preserve the requested completeness policy."
        )
    if stage == "reference_resolution":
        return (
            "Copy the exact current document_uid/object_name reference from the eligible Part, "
            "Mesh, or Points domain context; do not use labels, paths, or stale names."
        )
    if stage == "native_inspection":
        return (
            "Keep actual and nominal roles fixed, then increase search_radius only to the known "
            "deviation envelope or repair the reported invalid/empty source geometry."
        )
    if stage == "native_readback":
        return (
            "Keep the comparison definition unchanged and replace or repair only the native source "
            "that produced malformed distance samples before retrying."
        )
    if stage == "graph_membership":
        return (
            "Return every comparison used by group/measurement and every group used by report under "
            "its own declared stable output name; reuse the exact returned value."
        )
    if stage == "output_identity":
        return (
            "Remove only the duplicate output definition or make it a deliberately different "
            "comparison/derived result; each declared output must have one unique graph identity."
        )
    if stage == "measurement":
        return (
            "Choose measured_count or unmeasured_count when no samples were measured, or correct "
            "search_radius/source geometry before requesting a distance-valued metric."
        )
    if stage == "output_evaluation":
        return (
            "Return the missing prerequisite comparison/group before its derived measurement/report "
            "and preserve the declared graph order."
        )
    if stage == "artifact_export":
        return (
            "Keep the validated comparison unchanged and retry only after the isolated worker can "
            "write and authenticate its bounded float32 distance artifact."
        )
    return (
        "Correct only the reported Inspection source, tolerance/search setting, graph member, or "
        "declared output and retry the failed working revision; do not recreate the program."
    )


class InspectionCandidateError(RuntimeError):
    """A model-correctable Inspection failure with structured diagnostics."""

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


def _fail(
    message: str,
    *,
    stage: str,
    **details: Any,
) -> InspectionCandidateError:
    return InspectionCandidateError(message, details={"stage": stage, **details})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encoded(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"An Inspection definition is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc
    if len(payload) > _MAX_DEFINITION_BYTES:
        raise _fail(
            f"An Inspection definition exceeds {_MAX_DEFINITION_BYTES} JSON bytes.",
            stage="definition_contract",
            json_bytes=len(payload),
        )
    return payload


def _definition_key(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _inflate(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_inflate(item) for item in value)
    if not isinstance(value, Mapping):
        return value
    if set(value) == {
        "domain",
        "operation",
        "output_type",
        "arguments",
        "properties",
    }:
        return DomainValue(
            domain=str(value.get("domain") or ""),
            operation=str(value.get("operation") or ""),
            output_type=str(value.get("output_type") or ""),
            arguments=tuple(_inflate(item) for item in list(value.get("arguments") or [])),
            properties={
                str(name): _inflate(item)
                for name, item in dict(value.get("properties") or {}).items()
            },
        )
    return {str(name): _inflate(item) for name, item in value.items()}


def validate_inspection_definition(
    value: Any,
    *,
    expected_output_type: str | None = None,
    require_domain_value: bool = True,
    context: str = "definition",
) -> dict[str, Any]:
    """Replay one untrusted definition through the exact provider API."""

    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif not require_domain_value and isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be returned by the active Inspection api.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields or payload.get("domain") != "inspection":
        raise _fail(
            f"{context} has malformed Inspection definition fields.",
            stage="definition_contract",
            path=context,
        )
    operation = str(payload.get("operation") or "")
    output_type = str(payload.get("output_type") or "")
    if operation not in _OPERATION_OUTPUT or output_type != _OPERATION_OUTPUT[operation]:
        raise _fail(
            f"{context} has unsupported operation/type {operation!r}/{output_type!r}.",
            stage="definition_contract",
            path=context,
        )
    if expected_output_type is not None and output_type != expected_output_type:
        raise _fail(
            f"{context} must publish {expected_output_type!r}, not {output_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if not isinstance(arguments, list) or not isinstance(properties, Mapping):
        raise _fail(
            f"{context} arguments/properties must be an array and object.",
            stage="definition_contract",
            path=context,
        )
    properties = dict(properties)
    api = InspectionDomainAPI(_EXPORTS, _OUTPUT_TYPES)
    try:
        if operation == "comparison":
            if len(arguments) != 2 or set(properties) != {
                "search_radius",
                "tolerance",
                "thickness",
                "require_complete",
                "label",
            }:
                raise ValueError("comparison fields are malformed")
            rebuilt = api.comparison(
                arguments[0],
                arguments[1],
                search_radius=properties["search_radius"],
                tolerance=properties["tolerance"],
                thickness=properties["thickness"],
                require_complete=properties["require_complete"],
                label=properties["label"],
            )
        elif operation == "group":
            if len(arguments) != 1 or set(properties) != {"label"}:
                raise ValueError("group fields are malformed")
            raw_members = arguments[0]
            if not isinstance(raw_members, list):
                raise ValueError("group members must be an array")
            members = [
                _inflate(
                    validate_inspection_definition(
                        member,
                        expected_output_type="inspection_feature",
                        require_domain_value=False,
                        context=f"{context}.arguments[0][{index}]",
                    )
                )
                for index, member in enumerate(raw_members)
            ]
            rebuilt = api.group(members, label=properties["label"])
        elif operation == "measurement":
            if len(arguments) != 1 or set(properties) != {"metric", "label"}:
                raise ValueError("measurement fields are malformed")
            comparison = validate_inspection_definition(
                arguments[0],
                expected_output_type="inspection_feature",
                require_domain_value=False,
                context=f"{context}.arguments[0]",
            )
            rebuilt = api.measurement(
                _inflate(comparison),
                metric=properties["metric"],
                label=properties["label"],
            )
        else:
            if len(arguments) != 1 or set(properties) != {"label"}:
                raise ValueError("report fields are malformed")
            group = validate_inspection_definition(
                arguments[0],
                expected_output_type="inspection_group",
                require_domain_value=False,
                context=f"{context}.arguments[0]",
            )
            rebuilt = api.report(_inflate(group), label=properties["label"])
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"{context} is invalid: {exc}",
            stage="definition_contract",
            path=context,
            operation=operation,
        ) from exc
    canonical = rebuilt.to_payload()
    if canonical != payload:
        raise _fail(
            f"{context} is not the canonical api.{operation} representation.",
            stage="definition_contract",
            path=context,
        )
    _encoded(canonical)
    return canonical


def _bounded_artifact_path(root: Path, relative: Any, *, context: str) -> Path:
    resolved_root = Path(root).resolve()
    if not isinstance(relative, str) or not relative:
        raise _fail(
            f"{context} has no artifact path.",
            stage="reference_resolution",
            path=context,
        )
    path = (resolved_root / relative).resolve()
    if resolved_root not in path.parents or not path.is_file() or path.is_symlink():
        raise _fail(
            f"{context} artifact is missing, symlinked, or outside staging.",
            stage="reference_resolution",
            path=context,
        )
    size = path.stat().st_size
    if not 1 <= size <= 256 * 1024 * 1024:
        raise _fail(
            f"{context} artifact has an invalid size.",
            stage="reference_resolution",
            path=context,
            artifact_bytes=size,
        )
    return path


def _reference_key(
    entry: Mapping[str, Any],
    *,
    context: str,
    stage: str = "reference_resolution",
) -> tuple[str, str]:
    values = []
    for name in ("document_uid", "object_name"):
        raw = entry.get(name)
        if (
            not isinstance(raw, str)
            or not raw
            or raw != raw.strip()
            or len(raw) > 256
            or "\0" in raw
        ):
            raise _fail(
                f"{context}.{name} is invalid.",
                stage=stage,
                path=f"{context}.{name}",
            )
        values.append(raw)
    return values[0], values[1]


def configure_inspection_references(
    root: Path,
    document_references: list[dict[str, Any]],
) -> None:
    """Authenticate and import detached Part, Mesh, and Points inputs."""

    if len(document_references) > _MAX_REFERENCES:
        raise _fail(
            f"Inspection accepts at most {_MAX_REFERENCES} document references.",
            stage="reference_resolution",
            reference_count=len(document_references),
        )
    references: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(document_references):
        context = f"document_references[{index}]"
        if not isinstance(entry, dict):
            raise _fail(
                f"{context} must be an object.",
                stage="reference_resolution",
                path=context,
            )
        required = {
            "document_uid",
            "object_name",
            "artifact_kind",
            "artifact_path",
        }
        if not required <= set(entry) or set(entry) - required - _REFERENCE_OPTIONAL_FIELDS - {
            "brep_sha256",
            "mesh_sha256",
            "artifact_sha256",
        }:
            raise _fail(
                f"{context} has malformed fields.",
                stage="reference_resolution",
                path=context,
            )
        key = _reference_key(entry, context=context)
        if key in references:
            raise _fail(
                f"{context} duplicates document object {key[1]!r}.",
                stage="reference_resolution",
                path=context,
                object_name=key[1],
            )
        artifact_kind = str(entry.get("artifact_kind") or "")
        path = _bounded_artifact_path(root, entry.get("artifact_path"), context=context)
        digest_field = {
            "brep": "brep_sha256",
            "mesh_bms": "mesh_sha256",
            "points_asc": "artifact_sha256",
        }.get(artifact_kind)
        if digest_field is None:
            raise _fail(
                f"{context}.artifact_kind is unsupported.",
                stage="reference_resolution",
                path=f"{context}.artifact_kind",
                artifact_kind=artifact_kind,
            )
        digest = entry.get(digest_field)
        if not isinstance(digest, str) or _sha256_file(path) != digest:
            raise _fail(
                f"{context} SHA-256 does not match its descriptor.",
                stage="reference_resolution",
                path=context,
                object_name=key[1],
            )
        if artifact_kind == "brep":
            import Part

            geometry = Part.Shape()
            try:
                geometry.importBrep(str(path))
            except Exception as exc:
                raise _fail(
                    f"{context} BREP import failed: {type(exc).__name__}: {exc}",
                    stage="reference_resolution",
                    path=context,
                    exception_type=type(exc).__name__,
                ) from exc
            if geometry.isNull() or not geometry.isValid():
                raise _fail(
                    f"{context} contains an invalid BREP.",
                    stage="reference_resolution",
                    path=context,
                )
            native_type = "Part::Feature"
            sample_count = None
        elif artifact_kind == "mesh_bms":
            import Mesh

            try:
                geometry = Mesh.Mesh(str(path))
            except Exception as exc:
                raise _fail(
                    f"{context} Mesh import failed: {type(exc).__name__}: {exc}",
                    stage="reference_resolution",
                    path=context,
                    exception_type=type(exc).__name__,
                ) from exc
            if int(geometry.CountPoints) < 1 or int(geometry.CountFacets) < 1:
                raise _fail(
                    f"{context} contains an empty native Mesh.",
                    stage="reference_resolution",
                    path=context,
                )
            native_type = "Mesh::Feature"
            sample_count = int(geometry.CountPoints)
        else:
            import Points
            from vibescript_points_worker import load_point_attribute_artifacts

            try:
                geometry = Points.Points(str(path))
            except Exception as exc:
                raise _fail(
                    f"{context} Points import failed: {type(exc).__name__}: {exc}",
                    stage="reference_resolution",
                    path=context,
                    exception_type=type(exc).__name__,
                ) from exc
            sample_count = int(geometry.CountPoints)
            if not 1 <= sample_count <= _MAX_DISTANCE_COUNT:
                raise _fail(
                    f"{context} must contain 1-{_MAX_DISTANCE_COUNT} points.",
                    stage="reference_resolution",
                    path=context,
                    sample_count=sample_count,
                )
            try:
                load_point_attribute_artifacts(
                    root,
                    entry.get("attribute_artifacts", []),
                    point_count=sample_count,
                    context=f"{context}.attribute_artifacts",
                )
            except Exception as exc:
                details = getattr(exc, "details", None)
                raise _fail(
                    f"{context} point attributes are invalid: {exc}",
                    stage="reference_resolution",
                    path=f"{context}.attribute_artifacts",
                    source_error=(
                        dict(details) if isinstance(details, Mapping) else None
                    ),
                ) from exc
            native_type = "Points::Feature"
        references[key] = MappingProxyType(
            {
                "artifact_kind": artifact_kind,
                "artifact_sha256": digest,
                "geometry": geometry,
                "native_type": native_type,
                "sample_count": sample_count,
                "label": str(entry.get("label") or ""),
                "source_type_id": str(entry.get("type_id") or ""),
            }
        )
    global _REFERENCES
    _REFERENCES = MappingProxyType(references)


def _source_key(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _fail(
            f"{context} must contain exactly document_uid and object_name.",
            stage="reference_resolution",
            path=context,
        )
    key = _reference_key(value, context=context, stage="reference_resolution")
    if key not in _REFERENCES:
        raise _fail(
            f"{context} refers to unauthenticated object {key[1]!r}.",
            stage="reference_resolution",
            path=context,
            object_name=key[1],
        )
    return key


def _source_identity(key: tuple[str, str]) -> dict[str, Any]:
    source = _REFERENCES[key]
    return {
        "document_uid": key[0],
        "object_name": key[1],
        "artifact_kind": str(source["artifact_kind"]),
        "artifact_sha256": str(source["artifact_sha256"]),
        "source_type_id": str(source["source_type_id"]),
        "worker_native_type": str(source["native_type"]),
    }


def _source_object(
    document: Any,
    key: tuple[str, str],
    cache: dict[tuple[str, str], Any],
) -> Any:
    existing = cache.get(key)
    if existing is not None:
        return existing
    source = _REFERENCES[key]
    name = f"InspectionSource{len(cache):03d}"
    native_type = str(source["native_type"])
    obj = document.addObject(native_type, name)
    if native_type == "Part::Feature":
        obj.Shape = source["geometry"].copy()
    elif native_type == "Mesh::Feature":
        obj.Mesh = source["geometry"].copy()
    else:
        obj.Points = source["geometry"].copy()
    obj.Label = str(source["label"] or key[1])
    cache[key] = obj
    return obj


def _canonical_distances(values: Sequence[Any]) -> list[float]:
    result = array("f")
    for index, raw in enumerate(values):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _fail(
                f"Native Inspection distance {index} is not numeric.",
                stage="native_readback",
                distance_index=index,
            )
        value = float(raw)
        if not math.isfinite(value):
            raise _fail(
                f"Native Inspection distance {index} is not finite.",
                stage="native_readback",
                distance_index=index,
            )
        result.append(value)
    return [float(value) for value in result]


def summarize_distances(
    distances: Sequence[float],
    *,
    tolerance: Sequence[float],
    require_complete: bool,
) -> dict[str, Any]:
    """Return the canonical summary used by worker and host validation."""

    if not 1 <= len(distances) <= _MAX_DISTANCE_COUNT:
        raise ValueError(
            f"Inspection distance count must be 1-{_MAX_DISTANCE_COUNT}."
        )
    if not isinstance(tolerance, (list, tuple)) or len(tolerance) != 2:
        raise ValueError("Inspection tolerance must be [lower, upper].")
    lower, upper = (float(tolerance[0]), float(tolerance[1]))
    measured = [float(value) for value in distances if abs(float(value)) < _UNMEASURED_THRESHOLD]
    if any(not math.isfinite(value) for value in measured):
        raise ValueError("Inspection distances must be finite float32 values.")
    measured_count = len(measured)
    unmeasured_count = len(distances) - measured_count
    within = sum(lower <= value <= upper for value in measured)
    if measured:
        mean = math.fsum(measured) / measured_count
        rms = math.sqrt(math.fsum(value * value for value in measured) / measured_count)
        minimum: float | None = min(measured)
        maximum: float | None = max(measured)
        absolute_maximum: float | None = max(abs(value) for value in measured)
    else:
        minimum = None
        maximum = None
        mean = None
        rms = None
        absolute_maximum = None
    passed = bool(
        measured_count
        and within == measured_count
        and (not require_complete or unmeasured_count == 0)
    )
    return {
        "sample_count": len(distances),
        "measured_count": measured_count,
        "unmeasured_count": unmeasured_count,
        "minimum": minimum,
        "maximum": maximum,
        "mean": mean,
        "rms": rms,
        "absolute_maximum": absolute_maximum,
        "within_tolerance_count": within,
        "within_tolerance_fraction": (
            within / measured_count if measured_count else 0.0
        ),
        "passed": passed,
    }


def _write_distances(path: Path, distances: Sequence[float]) -> None:
    values = array("f", (float(value) for value in distances))
    if sys.byteorder != "little":
        values.byteswap()
    with path.open("wb") as handle:
        values.tofile(handle)


def _measurement_value(summary: Mapping[str, Any], metric: str) -> tuple[float, str]:
    if metric in {"measured_count", "unmeasured_count"}:
        return float(int(summary[metric])), "count"
    value = summary.get(metric)
    if value is None:
        raise _fail(
            f"Metric {metric!r} is unavailable because no actual samples were measured.",
            stage="measurement",
            metric=metric,
        )
    return (
        float(value),
        "ratio" if metric == "within_tolerance_fraction" else "mm",
    )


def validate_and_build_inspection(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compute native comparisons and serialize bounded distance artifacts."""

    expected_names = [str(item["name"]) for item in expected_outputs]
    if list(raw_result) != expected_names:
        raise _fail(
            "Inspection result keys must exactly match expected_outputs in declared order.",
            stage="result_contract",
            expected=expected_names,
            received=list(raw_result),
        )
    definitions: dict[str, dict[str, Any]] = {}
    keys: dict[str, str] = {}
    output_by_key: dict[str, tuple[str, str]] = {}
    for expected in expected_outputs:
        name = str(expected["name"])
        definition = validate_inspection_definition(
            raw_result[name],
            expected_output_type=str(expected["type"]),
            context=f"result.{name}",
        )
        key = _definition_key(definition)
        if key in output_by_key:
            raise _fail(
                f"Outputs {output_by_key[key][0]!r} and {name!r} return duplicate "
                "Inspection definitions.",
                stage="output_identity",
                output=name,
            )
        definitions[name] = definition
        keys[name] = key
        output_by_key[key] = (name, str(expected["type"]))

    import Inspection

    del Inspection
    source_objects: dict[tuple[str, str], Any] = {}
    records: dict[str, dict[str, Any]] = {}
    for name, definition in definitions.items():
        if definition["operation"] != "comparison":
            continue
        properties = dict(definition["properties"])
        actual_key = _source_key(
            definition["arguments"][0],
            context=f"result.{name}.actual",
        )
        nominal_keys = [
            _source_key(item, context=f"result.{name}.nominals[{index}]")
            for index, item in enumerate(definition["arguments"][1])
        ]
        actual = _source_object(document, actual_key, source_objects)
        nominals = [
            _source_object(document, key, source_objects) for key in nominal_keys
        ]
        try:
            feature = document.addObject(
                "Inspection::Feature", f"InspectionComparison{len(records):03d}"
            )
            feature.Actual = actual
            feature.Nominals = nominals
            feature.SearchRadius = float(properties["search_radius"])
            feature.Thickness = float(properties["thickness"])
            recomputed = int(document.recompute())
        except Exception as exc:
            raise _fail(
                f"Native Inspection comparison {name!r} failed: "
                f"{type(exc).__name__}: {exc}",
                stage="native_inspection",
                output=name,
                actual=_source_identity(actual_key),
                nominals=[_source_identity(key) for key in nominal_keys],
                search_radius=float(properties["search_radius"]),
                exception_type=type(exc).__name__,
            ) from exc
        if "Invalid" in set(feature.State) or "Error" in set(feature.State):
            raise _fail(
                f"Native Inspection comparison {name!r} failed: {feature.State}.",
                stage="native_inspection",
                output=name,
            )
        distances = _canonical_distances(list(feature.Distances))
        if not distances:
            raise _fail(
                f"Native Inspection comparison {name!r} produced no samples.",
                stage="native_inspection",
                output=name,
            )
        try:
            summary = summarize_distances(
                distances,
                tolerance=list(properties["tolerance"]),
                require_complete=bool(properties["require_complete"]),
            )
        except ValueError as exc:
            raise _fail(
                f"Native Inspection comparison {name!r} produced invalid readback: {exc}",
                stage="native_readback",
                output=name,
                distance_count=len(distances),
            ) from exc
        records[keys[name]] = {
            "object": feature,
            "distances": distances,
            "data": {
                "schema": VALIDATION_SCHEMA,
                "operation": "comparison",
                "label": str(properties.get("label") or name),
                "actual": _source_identity(actual_key),
                "nominals": [_source_identity(key) for key in nominal_keys],
                "search_radius": float(properties["search_radius"]),
                "thickness": float(properties["thickness"]),
                "tolerance": [float(value) for value in properties["tolerance"]],
                "require_complete": bool(properties["require_complete"]),
                "distance_summary": summary,
                "passed": bool(summary["passed"]),
                "native_trace": {
                    "engine": "Inspection::Feature",
                    "type_id": str(feature.TypeId),
                    "actual_type": str(actual.TypeId),
                    "nominal_types": [str(obj.TypeId) for obj in nominals],
                    "distance_count": len(distances),
                    "recomputed_object_count": recomputed,
                },
            },
        }

    for name, definition in definitions.items():
        if definition["operation"] != "group":
            continue
        member_keys = [_definition_key(item) for item in definition["arguments"][0]]
        members = []
        member_outputs = []
        for member_key in member_keys:
            target = output_by_key.get(member_key)
            record = records.get(member_key)
            if target is None or target[1] != "inspection_feature" or record is None:
                raise _fail(
                    f"Inspection group {name!r} contains a comparison that is not "
                    "returned as a declared inspection_feature output.",
                    stage="graph_membership",
                    output=name,
                )
            members.append(record["object"])
            member_outputs.append(target[0])
        group = document.addObject("Inspection::Group", f"InspectionGroup{len(records):03d}")
        for member in members:
            group.addObject(member)
        summaries = [records[key]["data"]["distance_summary"] for key in member_keys]
        passed_count = sum(bool(summary["passed"]) for summary in summaries)
        properties = dict(definition["properties"])
        records[keys[name]] = {
            "object": group,
            "data": {
                "schema": VALIDATION_SCHEMA,
                "operation": "group",
                "label": str(properties.get("label") or name),
                "member_outputs": member_outputs,
                "comparison_count": len(members),
                "passed_count": passed_count,
                "failed_count": len(members) - passed_count,
                "passed": passed_count == len(members),
                "native_trace": {
                    "engine": "Inspection::Group",
                    "type_id": str(group.TypeId),
                    "member_count": len(list(group.Group)),
                },
            },
        }

    for name, definition in definitions.items():
        if definition["operation"] != "measurement":
            continue
        target_key = _definition_key(definition["arguments"][0])
        target = output_by_key.get(target_key)
        record = records.get(target_key)
        if target is None or target[1] != "inspection_feature" or record is None:
            raise _fail(
                f"Measurement {name!r} targets a comparison that is not returned as "
                "a declared inspection_feature output.",
                stage="graph_membership",
                output=name,
            )
        properties = dict(definition["properties"])
        metric = str(properties["metric"])
        value, unit = _measurement_value(record["data"]["distance_summary"], metric)
        carrier = document.addObject("App::FeaturePython", f"InspectionMeasurement{len(records):03d}")
        carrier.addProperty("App::PropertyString", "Metric")
        carrier.addProperty("App::PropertyFloat", "Value")
        carrier.addProperty("App::PropertyString", "Unit")
        carrier.Metric = metric
        carrier.Value = value
        carrier.Unit = unit
        records[keys[name]] = {
            "object": carrier,
            "data": {
                "schema": VALIDATION_SCHEMA,
                "operation": "measurement",
                "label": str(properties.get("label") or name),
                "target_output": target[0],
                "metric": metric,
                "value": value,
                "unit": unit,
                "passed": bool(record["data"]["distance_summary"]["passed"]),
                "native_trace": {
                    "engine": "App::FeaturePython",
                    "type_id": str(carrier.TypeId),
                    "metric": str(carrier.Metric),
                    "value": float(carrier.Value),
                    "unit": str(carrier.Unit),
                },
            },
        }

    for name, definition in definitions.items():
        if definition["operation"] != "report":
            continue
        group_key = _definition_key(definition["arguments"][0])
        group_target = output_by_key.get(group_key)
        group_record = records.get(group_key)
        if (
            group_target is None
            or group_target[1] != "inspection_group"
            or group_record is None
        ):
            raise _fail(
                f"Report {name!r} targets a group that is not returned as a declared "
                "inspection_group output.",
                stage="graph_membership",
                output=name,
            )
        entries = []
        for output_name in group_record["data"]["member_outputs"]:
            member_record = records[keys[output_name]]
            entries.append(
                {
                    "output": output_name,
                    "label": str(member_record["data"]["label"]),
                    "passed": bool(member_record["data"]["distance_summary"]["passed"]),
                    "distance_summary": dict(member_record["data"]["distance_summary"]),
                }
            )
        properties = dict(definition["properties"])
        carrier = document.addObject("App::FeaturePython", f"InspectionReport{len(records):03d}")
        carrier.addProperty("App::PropertyBool", "Passed")
        carrier.addProperty("App::PropertyInteger", "ComparisonCount")
        carrier.Passed = bool(group_record["data"]["passed"])
        carrier.ComparisonCount = int(group_record["data"]["comparison_count"])
        records[keys[name]] = {
            "object": carrier,
            "data": {
                "schema": VALIDATION_SCHEMA,
                "operation": "report",
                "label": str(properties.get("label") or name),
                "group_output": group_target[0],
                "comparison_count": int(group_record["data"]["comparison_count"]),
                "passed_count": int(group_record["data"]["passed_count"]),
                "failed_count": int(group_record["data"]["failed_count"]),
                "passed": bool(group_record["data"]["passed"]),
                "entries": entries,
                "native_trace": {
                    "engine": "App::FeaturePython",
                    "type_id": str(carrier.TypeId),
                    "comparison_count": int(carrier.ComparisonCount),
                    "passed": bool(carrier.Passed),
                },
            },
        }

    outputs = []
    summaries = []
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        output_type = str(expected["type"])
        definition = definitions[name]
        record = records.get(keys[name])
        if record is None:
            raise _fail(
                f"Inspection output {name!r} was not evaluated.",
                stage="output_evaluation",
                output=name,
            )
        item: dict[str, Any] = {
            "name": name,
            "type": output_type,
            "definition": definition,
            "inspection_data": record["data"],
        }
        artifact_digest = ""
        if output_type == "inspection_feature":
            distances = record["distances"]
            relative = Path("outputs") / f"output-{index:03d}-distances.f32"
            target = root / relative
            try:
                _write_distances(target, distances)
            except Exception as exc:
                raise _fail(
                    f"Inspection output {name!r} distance export failed: "
                    f"{type(exc).__name__}: {exc}",
                    stage="artifact_export",
                    output=name,
                    exception_type=type(exc).__name__,
                ) from exc
            expected_bytes = len(distances) * 4
            if target.stat().st_size != expected_bytes or expected_bytes > _MAX_ARTIFACT_BYTES:
                raise _fail(
                    f"Inspection output {name!r} serialized an invalid distance artifact.",
                    stage="artifact_export",
                    output=name,
                )
            artifact_digest = _sha256_file(target)
            item.update(
                {
                    "artifact_kind": "inspection_distances_f32le",
                    "artifact_schema": DISTANCE_SCHEMA,
                    "artifact_path": str(relative),
                    "artifact_sha256": artifact_digest,
                    "artifact_bytes": expected_bytes,
                    "distance_count": len(distances),
                }
            )
        _encoded(record["data"])
        outputs.append(item)
        summaries.append(
            {
                "name": name,
                "type": output_type,
                "operation": str(definition["operation"]),
                "definition_sha256": keys[name],
                "artifact_sha256": artifact_digest,
                "passed": bool(record["data"].get("passed", True)),
            }
        )
    validation = {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "reference_count": len(_REFERENCES),
        "outputs": summaries,
    }
    _encoded(validation)
    return outputs, validation
