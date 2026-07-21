# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native Mesh evaluator for production Mesh VibeScript."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue


VALIDATION_SCHEMA = "vibecad-vibescript-mesh-validation-v1"
_OPERATIONS = ("mesh", "from_object", "transform", "repair", "diagnostics")
_PROPERTY_NAMES = {
    "mesh": {"label"},
    "from_object": {"label"},
    "transform": {"translation", "rotation", "scale", "label"},
    "repair": {
        "remove_duplicate_points",
        "remove_duplicate_facets",
        "fix_degenerations",
        "remove_non_manifolds",
        "fix_self_intersections",
        "fill_holes_max_edges",
        "harmonize_normals",
        "decimate_reduction",
        "decimate_tolerance",
        "label",
    },
    "diagnostics": {
        "require_solid",
        "require_closed",
        "require_manifold",
        "require_consistent_orientation",
        "require_no_self_intersections",
        "max_components",
        "max_open_edges",
        "label",
    },
}
_ARGUMENT_COUNTS = {
    "mesh": 1,
    "from_object": 1,
    "transform": 1,
    "repair": 1,
    "diagnostics": 1,
}
_MAX_DEFINITION_DEPTH = 32
_MAX_DEFINITION_BYTES = 1_000_000
_MAX_SELF_INTERSECTION_SAMPLE = 64
_MAX_SELF_INTERSECTION_DETAIL_FACETS = 128
_MAX_ABS_COORDINATE = 1_000_000_000.0
_MAX_REFERENCE_COUNT = 128
_MAX_REFERENCE_BYTES = 256 * 1024 * 1024
_MAX_REFERENCE_FACETS = 2_000_000
_MAX_REFERENCE_SEGMENTS = 4096

_REFERENCE_MESHES: Mapping[tuple[str, str], Any] = MappingProxyType({})
_REFERENCE_METADATA: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded model repair for every Mesh failure stage."""

    stage = str(details.get("stage") or "")
    operation = str(details.get("operation") or "")
    path = str(details.get("path") or "")
    output_name = str(details.get("output_name") or "")
    location = f" at {path}" if path else (f" {output_name!r}" if output_name else "")
    if stage == "result_contract":
        return (
            "Return exactly the declared expected_outputs names, types, and order. "
            "Replace only the mismatched result value and keep the declarations stable."
        )
    if stage == "definition_contract":
        return (
            f"Rebuild only the malformed Mesh graph value{location} with the active api; "
            "never construct, copy, or mutate serialized operation dictionaries."
        )
    if stage == "api_revalidation":
        api_name = f"api.{operation}" if operation else "the reported Mesh api call"
        return (
            f"Change only the invalid argument in {api_name}{location} to match describe_api, "
            "then retry the failed working revision."
        )
    if stage in {"reference_contract", "reference_selection"}:
        return (
            "Choose one exact eligible Mesh::Feature reference from document_meshes, pass its "
            "unchanged document_uid and object_name through an x-vibecad-reference input, and "
            "call api.from_object with that input."
        )
    if stage == "native_input":
        return (
            f"Correct only the reported finite coordinate, transform, or mesh value{location} "
            "so it stays inside the documented Mesh bounds."
        )
    if stage == "diagnostic_requirements":
        return (
            "Read failures and diagnostics, add or adjust only the repair pass that addresses "
            "the reported defect, then preserve the requirement unchanged "
            "unless the human request explicitly permits relaxing it."
        )
    if stage in {"native_diagnostics", "native_operation"}:
        api_name = f"api.{operation}" if operation else "the reported Mesh operation"
        return (
            f"Change only {api_name} or its immediate source based on the reported native "
            "exception and before/after topology; do not add unrelated repair passes."
        )
    if stage == "artifact_export":
        return (
            "Keep the validated Mesh source unchanged and retry only after the isolated worker "
            "can write and authenticate its bounded project staging artifact."
        )
    return (
        "Correct only the reported Mesh operation and retry the failed working revision; "
        "do not recreate the program or change unrelated outputs."
    )


class MeshCandidateError(RuntimeError):
    """A provider-correctable Mesh failure with structured diagnostics."""

    def __init__(
        self, message: str, *, details: Mapping[str, Any] | None = None
    ) -> None:
        self.details = dict(details or {})
        if not str(self.details.get("correction") or "").strip():
            self.details["correction"] = _default_correction(self.details)
        super().__init__(message)


def _fail(message: str, *, stage: str, **details: Any) -> MeshCandidateError:
    return MeshCandidateError(message, details={"stage": stage, **details})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reference_key(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _fail(
            f"{context} must contain exactly document_uid and object_name.",
            stage="reference_contract",
            path=context,
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
        raise _fail(
            f"{context}.document_uid is not a bounded stable document identity.",
            stage="reference_contract",
            path=f"{context}.document_uid",
        )
    if (
        not isinstance(object_name, str)
        or not object_name
        or object_name != object_name.strip()
        or len(object_name) > 256
        or "\0" in object_name
    ):
        raise _fail(
            f"{context}.object_name is not a bounded stable object identity.",
            stage="reference_contract",
            path=f"{context}.object_name",
        )
    return document_uid, object_name


def _reference_values_match(reported: Any, observed: Any, *, path: str) -> None:
    if isinstance(observed, bool):
        matches = type(reported) is bool and reported is observed
    elif isinstance(observed, int):
        matches = type(reported) is int and reported == observed
    elif isinstance(observed, float):
        matches = (
            isinstance(reported, (int, float))
            and not isinstance(reported, bool)
            and math.isfinite(float(reported))
            and math.isclose(float(reported), observed, rel_tol=1.0e-9, abs_tol=1.0e-9)
        )
    elif isinstance(observed, str) or observed is None:
        matches = reported == observed
    elif isinstance(observed, list):
        if not isinstance(reported, list) or len(reported) != len(observed):
            matches = False
        else:
            for index, (left, right) in enumerate(zip(reported, observed)):
                _reference_values_match(left, right, path=f"{path}[{index}]")
            return
    elif isinstance(observed, Mapping):
        if not isinstance(reported, Mapping) or set(reported) != set(observed):
            matches = False
        else:
            for key, value in observed.items():
                _reference_values_match(reported[key], value, path=f"{path}.{key}")
            return
    else:
        matches = False
    if not matches:
        raise _fail(
            f"{path} differs from the authenticated native Mesh snapshot.",
            stage="reference_contract",
            path=path,
        )


def _bounded_segments(value: Any, *, facet_count: int, context: str) -> list[list[int]]:
    if not isinstance(value, list) or len(value) > _MAX_REFERENCE_SEGMENTS:
        raise _fail(
            f"{context} must contain at most {_MAX_REFERENCE_SEGMENTS} segment arrays.",
            stage="reference_contract",
            path=context,
        )
    result: list[list[int]] = []
    memberships = 0
    for segment_index, raw_segment in enumerate(value):
        if not isinstance(raw_segment, list):
            raise _fail(
                f"{context}[{segment_index}] must be an array of facet indices.",
                stage="reference_contract",
                path=f"{context}[{segment_index}]",
            )
        segment = []
        for facet_index, raw_index in enumerate(raw_segment):
            if type(raw_index) is not int or raw_index < 0 or raw_index >= facet_count:
                raise _fail(
                    f"{context}[{segment_index}][{facet_index}] is outside the mesh.",
                    stage="reference_contract",
                    path=f"{context}[{segment_index}][{facet_index}]",
                )
            segment.append(raw_index)
        memberships += len(segment)
        if memberships > max(facet_count * 4, facet_count + 1024):
            raise _fail(
                f"{context} contains excessive segment membership data.",
                stage="reference_contract",
                path=context,
            )
        result.append(segment)
    return result


def configure_mesh_references(root: Path, entries: list[dict[str, Any]]) -> None:
    """Load and authenticate placement-baked Mesh::Feature snapshots."""

    import Mesh

    if len(entries) > _MAX_REFERENCE_COUNT:
        raise _fail(
            f"Mesh accepts at most {_MAX_REFERENCE_COUNT} document references.",
            stage="reference_contract",
        )
    resolved_root = Path(root).resolve()
    meshes: dict[tuple[str, str], Any] = {}
    metadata: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise _fail(
                f"document_references[{index}] must be an object.",
                stage="reference_contract",
                path=f"document_references[{index}]",
            )
        identity = {
            "document_uid": entry.get("document_uid"),
            "object_name": entry.get("object_name"),
        }
        key = _reference_key(identity, context=f"document_references[{index}]")
        if key in metadata:
            raise _fail(
                f"document_references[{index}] duplicates {key[1]!r}.",
                stage="reference_contract",
                path=f"document_references[{index}]",
            )
        if str(entry.get("artifact_kind") or "") != "mesh_bms":
            raise _fail(
                f"document_references[{index}] is not a Mesh BMS snapshot.",
                stage="reference_selection",
                reference=identity,
                actual_artifact_kind=str(entry.get("artifact_kind") or ""),
            )
        type_id = str(entry.get("type_id") or "")
        if not type_id.startswith("Mesh::"):
            raise _fail(
                f"document_references[{index}] type {type_id!r} is not a native Mesh feature.",
                stage="reference_selection",
                reference=identity,
                actual_type_id=type_id,
            )
        path = (resolved_root / str(entry.get("artifact_path") or "")).resolve()
        if resolved_root not in path.parents or not path.is_file():
            raise _fail(
                f"document_references[{index}] artifact is missing or outside staging.",
                stage="reference_contract",
                path=f"document_references[{index}].artifact_path",
            )
        artifact_bytes = path.stat().st_size
        if not 1 <= artifact_bytes <= _MAX_REFERENCE_BYTES:
            raise _fail(
                f"document_references[{index}] artifact size is outside 1-"
                f"{_MAX_REFERENCE_BYTES} bytes.",
                stage="reference_contract",
                artifact_bytes=artifact_bytes,
            )
        digest = _sha256_file(path)
        if digest != str(entry.get("mesh_sha256") or ""):
            raise _fail(
                f"document_references[{index}] SHA-256 does not match the host snapshot.",
                stage="reference_contract",
                path=f"document_references[{index}].mesh_sha256",
            )
        try:
            mesh = Mesh.Mesh(str(path))
        except Exception as exc:
            raise _fail(
                f"document_references[{index}] could not be imported as BMS: {exc}",
                stage="reference_contract",
                exception_type=type(exc).__name__,
            ) from exc
        facet_count = int(mesh.CountFacets)
        if not 1 <= facet_count <= _MAX_REFERENCE_FACETS:
            raise _fail(
                f"document_references[{index}] contains {facet_count} facets; the "
                f"accepted range is 1-{_MAX_REFERENCE_FACETS}.",
                stage="reference_contract",
                facet_count=facet_count,
            )
        expected_segments = _bounded_segments(
            entry.get("mesh_segments", []),
            facet_count=facet_count,
            context=f"document_references[{index}].mesh_segments",
        )
        imported_segment_count = int(mesh.countSegments())
        if imported_segment_count > _MAX_REFERENCE_SEGMENTS:
            raise _fail(
                f"document_references[{index}] BMS contains {imported_segment_count} "
                f"segments; the limit is {_MAX_REFERENCE_SEGMENTS}.",
                stage="reference_contract",
                segment_count=imported_segment_count,
            )
        imported_segments = _bounded_segments(
            [
                [int(item) for item in list(mesh.getSegment(segment_index) or [])]
                for segment_index in range(imported_segment_count)
            ],
            facet_count=facet_count,
            context=f"document_references[{index}].imported_mesh_segments",
        )
        if imported_segments and imported_segments != expected_segments:
            raise _fail(
                f"document_references[{index}] segment groups differ from host metadata.",
                stage="reference_contract",
                path=f"document_references[{index}].mesh_segments",
            )
        if not imported_segments:
            for segment in expected_segments:
                mesh.addSegment(segment)
        restored_segments = _bounded_segments(
            [
                [int(item) for item in list(mesh.getSegment(segment_index) or [])]
                for segment_index in range(int(mesh.countSegments()))
            ],
            facet_count=facet_count,
            context=f"document_references[{index}].restored_mesh_segments",
        )
        if restored_segments != expected_segments:
            raise _fail(
                f"document_references[{index}] could not restore authenticated segments.",
                stage="reference_contract",
                path=f"document_references[{index}].mesh_segments",
            )
        diagnostics = mesh_diagnostics(mesh)
        reported_facts = entry.get("facts")
        if not isinstance(reported_facts, Mapping):
            raise _fail(
                f"document_references[{index}] has no authenticated Mesh facts.",
                stage="reference_contract",
                path=f"document_references[{index}].facts",
            )
        _reference_values_match(
            reported_facts,
            diagnostics,
            path=f"document_references[{index}].facts",
        )
        placement_matrix = entry.get("mesh_source_placement_matrix", [])
        if (
            not isinstance(placement_matrix, list)
            or len(placement_matrix) != 16
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in placement_matrix
            )
        ):
            raise _fail(
                f"document_references[{index}] source placement matrix is malformed.",
                stage="reference_contract",
                path=f"document_references[{index}].mesh_source_placement_matrix",
            )
        clean_metadata = {
            "document_uid": key[0],
            "object_name": key[1],
            "label": str(entry.get("label") or ""),
            "type_id": type_id,
            "artifact_kind": "mesh_bms",
            "artifact_sha256": digest,
            "source_kind": str(entry.get("source_kind") or ""),
            "source_program_id": str(entry.get("source_program_id") or ""),
            "source_program_domain": str(entry.get("source_program_domain") or ""),
            "source_revision": str(entry.get("source_revision") or ""),
            "transient_topology": bool(entry.get("transient_topology")),
            "source_placement_matrix": [float(value) for value in placement_matrix],
            "facts": diagnostics,
        }
        meshes[key] = mesh
        metadata[key] = MappingProxyType(clean_metadata)
    global _REFERENCE_MESHES, _REFERENCE_METADATA
    _REFERENCE_MESHES = MappingProxyType(meshes)
    _REFERENCE_METADATA = MappingProxyType(metadata)


def detached_reference_mesh(reference: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Return one authenticated detached mesh and its bounded source identity."""

    key = _reference_key(reference, context="api.from_object.reference")
    mesh = _REFERENCE_MESHES.get(key)
    if mesh is None:
        raise _fail(
            f"Reference {key[1]!r} is not an authenticated Mesh::Feature input.",
            stage="reference_selection",
            reference={"document_uid": key[0], "object_name": key[1]},
        )
    return mesh.copy(), dict(_REFERENCE_METADATA[key])


def _encoded(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"A Mesh definition is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc


def _payload(
    value: Any,
    *,
    context: str,
    require_domain_value: bool,
) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping) and not require_domain_value:
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be a value returned by the active Mesh api.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields:
        raise _fail(
            f"{context} has malformed Mesh definition fields.",
            stage="definition_contract",
            path=context,
            missing=sorted(fields - set(payload)),
            unexpected=sorted(set(payload) - fields),
        )
    operation = str(payload.get("operation") or "")
    if (
        payload.get("domain") != "mesh"
        or payload.get("output_type") != "mesh"
        or operation not in _OPERATIONS
    ):
        raise _fail(
            f"{context} is not a supported Mesh graph value.",
            stage="definition_contract",
            path=context,
            domain=payload.get("domain"),
            operation=operation,
            output_type=payload.get("output_type"),
        )
    if not isinstance(payload.get("arguments"), list) or not isinstance(
        payload.get("properties"), dict
    ):
        raise _fail(
            f"{context} arguments and properties must be serialized containers.",
            stage="definition_contract",
            path=context,
        )
    return payload


def _value_from_payload(payload: Mapping[str, Any]) -> DomainValue:
    return DomainValue(
        domain="mesh",
        operation=str(payload["operation"]),
        output_type="mesh",
        arguments=tuple(payload["arguments"]),
        properties=dict(payload["properties"]),
    )


def validate_mesh_definition(
    raw: Any,
    *,
    require_domain_value: bool,
    context: str = "result",
    depth: int = 0,
) -> dict[str, Any]:
    """Reconstruct every nested operation through the public Mesh API."""

    from vibescript_mesh_api import MeshDomainAPI

    if depth > _MAX_DEFINITION_DEPTH:
        raise _fail(
            f"{context} exceeds the Mesh operation-depth limit.",
            stage="definition_contract",
            path=context,
            maximum_depth=_MAX_DEFINITION_DEPTH,
        )
    payload = _payload(
        raw,
        context=context,
        require_domain_value=require_domain_value,
    )
    encoded_payload = _encoded(payload)
    if depth == 0 and len(encoded_payload.encode("utf-8")) > _MAX_DEFINITION_BYTES:
        raise _fail(
            f"{context} exceeds the {_MAX_DEFINITION_BYTES}-byte Mesh definition limit.",
            stage="definition_contract",
            path=context,
            maximum_bytes=_MAX_DEFINITION_BYTES,
        )
    operation = str(payload["operation"])
    arguments = list(payload["arguments"])
    properties = dict(payload["properties"])
    if (
        len(arguments) != _ARGUMENT_COUNTS[operation]
        or set(properties) != _PROPERTY_NAMES[operation]
    ):
        raise _fail(
            f"{context} has a malformed api.{operation} definition.",
            stage="definition_contract",
            path=context,
            expected_argument_count=_ARGUMENT_COUNTS[operation],
            received_argument_count=len(arguments),
            missing_properties=sorted(_PROPERTY_NAMES[operation] - set(properties)),
            unexpected_properties=sorted(set(properties) - _PROPERTY_NAMES[operation]),
        )
    api = MeshDomainAPI(_OPERATIONS, ("mesh",))
    try:
        if operation == "mesh":
            value = api.mesh(arguments[0], **properties)
        elif operation == "from_object":
            value = api.from_object(arguments[0], **properties)
        else:
            parent_payload = validate_mesh_definition(
                arguments[0],
                require_domain_value=False,
                context=f"{context}.arguments[0]",
                depth=depth + 1,
            )
            parent = _value_from_payload(parent_payload)
            value = getattr(api, operation)(parent, **properties)
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"{context} failed api.{operation} validation: {exc}",
            stage="api_revalidation",
            path=context,
            operation=operation,
            exception_type=type(exc).__name__,
        ) from exc
    canonical = value.to_payload()
    if _encoded(canonical) != encoded_payload:
        raise _fail(
            f"{context} differs from the canonical api.{operation} definition.",
            stage="api_revalidation",
            path=context,
            operation=operation,
        )
    return canonical


def _finite(value: Any, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{path} must be finite.", stage="native_input", path=path)
    clean = float(value)
    if not math.isfinite(clean):
        raise _fail(f"{path} must be finite.", stage="native_input", path=path)
    return clean


def _matrix(properties: Mapping[str, Any]):
    import FreeCAD as App

    translation = list(properties["translation"])
    rotation = list(properties["rotation"])
    scale = list(properties["scale"])
    placement = App.Placement(
        App.Vector(*(_finite(value, path="translation") for value in translation)),
        App.Rotation(*(_finite(value, path="rotation") for value in rotation)),
    )
    matrix = placement.toMatrix()
    for row in (1, 2, 3):
        for column, factor in enumerate(scale, start=1):
            name = f"A{row}{column}"
            setattr(
                matrix,
                name,
                float(getattr(matrix, name)) * _finite(factor, path="scale"),
            )
    return matrix


def _bounds(mesh: Any) -> dict[str, list[float]]:
    box = mesh.BoundBox
    return {
        "minimum": [float(box.XMin), float(box.YMin), float(box.ZMin)],
        "maximum": [float(box.XMax), float(box.YMax), float(box.ZMax)],
    }


def _self_intersection_sample(
    mesh: Any,
) -> tuple[bool, int | None, list[Any], bool]:
    has_intersections = bool(mesh.hasSelfIntersections())
    if not has_intersections:
        return False, 0, [], False
    if int(mesh.CountFacets) > _MAX_SELF_INTERSECTION_DETAIL_FACETS:
        return True, None, [], True
    raw = list(mesh.getSelfIntersections() or [])

    def clean(item: Any) -> list[Any]:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            raise TypeError("native self-intersection detail is malformed")
        return [
            int(item[0]),
            int(item[1]),
            [float(item[2].x), float(item[2].y), float(item[2].z)],
            [float(item[3].x), float(item[3].y), float(item[3].z)],
        ]

    sample = [clean(value) for value in raw[:_MAX_SELF_INTERSECTION_SAMPLE]]
    return True, len(raw), sample, len(raw) > len(sample)


def mesh_diagnostics(mesh: Any) -> dict[str, Any]:
    """Return bounded deterministic native topology diagnostics for one mesh."""

    try:
        (
            has_self_intersections,
            intersection_count,
            intersection_sample,
            intersection_truncated,
        ) = _self_intersection_sample(mesh)
        center = mesh.CenterOfGravity
        points = int(mesh.CountPoints)
        facets = int(mesh.CountFacets)
        edges = int(mesh.CountEdges)
        non_uniform_oriented_facets = int(mesh.countNonUniformOrientedFacets())
        result = {
            "points": points,
            "facets": facets,
            "edges": edges,
            "components": int(mesh.countComponents()),
            "open_edges": int(mesh.countOpenEdges()),
            "degenerated_facets": int(mesh.countDegeneratedFacets()),
            "duplicated_facets": int(mesh.countDuplicatedFacets()),
            "duplicated_points": int(mesh.countDuplicatedPoints()),
            "non_uniform_oriented_facets": non_uniform_oriented_facets,
            "has_non_manifolds": bool(mesh.hasNonManifolds()),
            "has_non_uniform_orientation": non_uniform_oriented_facets > 0,
            "has_self_intersections": has_self_intersections,
            "self_intersection_count": intersection_count,
            "self_intersection_sample": intersection_sample,
            "self_intersection_sample_truncated": intersection_truncated,
            "self_intersection_details_available": intersection_count is not None,
            "is_solid": bool(mesh.isSolid()),
            "has_corrupted_facets": bool(mesh.hasCorruptedFacets()),
            "has_facets_out_of_range": bool(mesh.hasFacetsOutOfRange()),
            "has_invalid_neighbourhood": bool(mesh.hasInvalidNeighbourhood()),
            "has_invalid_points": bool(mesh.hasInvalidPoints()),
            "has_points_out_of_range": bool(mesh.hasPointsOutOfRange()),
            "has_points_on_edge": bool(mesh.hasPointsOnEdge()),
            "area_mm2": float(mesh.Area),
            "volume_mm3": float(mesh.Volume),
            "center_of_gravity": [
                float(center.x),
                float(center.y),
                float(center.z),
            ],
            "bounds": _bounds(mesh),
            "euler_characteristic": points - edges + facets,
        }
    except Exception as exc:
        raise _fail(
            f"Native Mesh diagnostics failed: {exc}",
            stage="native_diagnostics",
            exception_type=type(exc).__name__,
        ) from exc
    if result["points"] <= 0 or result["facets"] <= 0:
        raise _fail(
            "The native Mesh result contains no publishable facets.",
            stage="native_diagnostics",
            points=result["points"],
            facets=result["facets"],
        )
    finite_values = [
        result["area_mm2"],
        result["volume_mm3"],
        *result["center_of_gravity"],
        *result["bounds"]["minimum"],
        *result["bounds"]["maximum"],
    ]
    for value in finite_values:
        if not math.isfinite(float(value)):
            raise _fail(
                "The native Mesh diagnostics contain non-finite geometry.",
                stage="native_diagnostics",
            )
    if any(
        abs(float(value)) > _MAX_ABS_COORDINATE
        for value in (
            *result["bounds"]["minimum"],
            *result["bounds"]["maximum"],
        )
    ):
        raise _fail(
            "The native Mesh result exceeds the supported coordinate bounds.",
            stage="native_diagnostics",
            maximum_absolute_coordinate=_MAX_ABS_COORDINATE,
            bounds=result["bounds"],
        )
    return result


def _quick_counts(mesh: Any) -> dict[str, Any]:
    return {
        "points": int(mesh.CountPoints),
        "facets": int(mesh.CountFacets),
        "open_edges": int(mesh.countOpenEdges()),
        "degenerated_facets": int(mesh.countDegeneratedFacets()),
        "duplicated_facets": int(mesh.countDuplicatedFacets()),
        "duplicated_points": int(mesh.countDuplicatedPoints()),
        "components": int(mesh.countComponents()),
    }


def _requirements(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: properties[key]
        for key in (
            "require_solid",
            "require_closed",
            "require_manifold",
            "require_consistent_orientation",
            "require_no_self_intersections",
            "max_components",
            "max_open_edges",
        )
    }


def _enforce_requirements(
    diagnostics: Mapping[str, Any],
    requirements: Mapping[str, Any],
) -> None:
    failures = []
    if requirements["require_solid"] and not diagnostics["is_solid"]:
        failures.append("mesh is not a solid")
    if requirements["require_closed"] and int(diagnostics["open_edges"]) != 0:
        failures.append(f"mesh has {diagnostics['open_edges']} open edges")
    if requirements["require_manifold"] and (
        diagnostics["has_non_manifolds"] or diagnostics["has_invalid_neighbourhood"]
    ):
        failures.append("mesh contains non-manifold or invalid neighbourhood topology")
    if (
        requirements["require_consistent_orientation"]
        and diagnostics["has_non_uniform_orientation"]
    ):
        failures.append("mesh contains inconsistently oriented facets")
    if (
        requirements["require_no_self_intersections"]
        and diagnostics["has_self_intersections"]
    ):
        failures.append("mesh contains self-intersections")
    max_components = requirements["max_components"]
    if max_components is not None and int(diagnostics["components"]) > int(
        max_components
    ):
        failures.append(
            f"mesh has {diagnostics['components']} components; maximum is {max_components}"
        )
    max_open_edges = requirements["max_open_edges"]
    if max_open_edges is not None and int(diagnostics["open_edges"]) > int(
        max_open_edges
    ):
        failures.append(
            f"mesh has {diagnostics['open_edges']} open edges; maximum is {max_open_edges}"
        )
    if failures:
        raise _fail(
            f"Mesh diagnostics requirements failed: {'; '.join(failures)}.",
            stage="diagnostic_requirements",
            failures=failures,
            requirements=dict(requirements),
            diagnostics=dict(diagnostics),
        )


def _source_identity(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: metadata.get(key)
        for key in (
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
            "source_placement_matrix",
        )
        if metadata.get(key) not in (None, "", [])
    }


def _evaluate_definition(
    payload: Mapping[str, Any],
    cache: dict[str, tuple[Any, list[dict[str, Any]]]],
) -> tuple[Any, list[dict[str, Any]]]:
    import Mesh

    digest = hashlib.sha256(_encoded(payload).encode("utf-8")).hexdigest()
    cached = cache.get(digest)
    if cached is not None:
        return cached[0].copy(), [dict(item) for item in cached[1]]
    operation = str(payload["operation"])
    arguments = list(payload["arguments"])
    properties = dict(payload["properties"])
    try:
        if operation == "mesh":
            mesh = Mesh.Mesh(arguments[0])
            trace = [
                {
                    "operation": "mesh",
                    "input_facets": len(arguments[0]),
                    "result": _quick_counts(mesh),
                }
            ]
        elif operation == "from_object":
            mesh, source_metadata = detached_reference_mesh(arguments[0])
            source_diagnostics = dict(source_metadata["facts"])
            trace = [
                {
                    "operation": "from_object",
                    "source": _source_identity(source_metadata),
                    "source_diagnostics": source_diagnostics,
                    "result": _quick_counts(mesh),
                }
            ]
        else:
            source_payload = validate_mesh_definition(
                arguments[0],
                require_domain_value=False,
                context=f"api.{operation}.source",
            )
            mesh, trace = _evaluate_definition(source_payload, cache)
            before = _quick_counts(mesh)
            if operation == "transform":
                mesh.transformGeometry(_matrix(properties))
                trace.append(
                    {
                        "operation": operation,
                        "translation": list(properties["translation"]),
                        "rotation": list(properties["rotation"]),
                        "scale": list(properties["scale"]),
                        "before": before,
                        "after": _quick_counts(mesh),
                    }
                )
            elif operation == "repair":
                applied = []
                for enabled, method_name, label in (
                    (
                        properties["remove_duplicate_points"],
                        "removeDuplicatedPoints",
                        "remove_duplicate_points",
                    ),
                    (
                        properties["remove_duplicate_facets"],
                        "removeDuplicatedFacets",
                        "remove_duplicate_facets",
                    ),
                    (
                        properties["fix_degenerations"],
                        "fixDegenerations",
                        "fix_degenerations",
                    ),
                    (
                        properties["remove_non_manifolds"],
                        "removeNonManifolds",
                        "remove_non_manifolds",
                    ),
                    (
                        properties["remove_non_manifolds"],
                        "removeNonManifoldPoints",
                        "remove_non_manifold_points",
                    ),
                    (
                        properties["fix_self_intersections"],
                        "fixSelfIntersections",
                        "fix_self_intersections",
                    ),
                ):
                    if enabled:
                        getattr(mesh, method_name)()
                        applied.append(label)
                hole_limit = int(properties["fill_holes_max_edges"])
                if hole_limit > 0:
                    mesh.fillupHoles(hole_limit)
                    applied.append("fill_holes")
                reduction = float(properties["decimate_reduction"])
                if reduction > 0.0:
                    mesh.decimate(float(properties["decimate_tolerance"]), reduction)
                    applied.append("decimate")
                mesh.rebuildNeighbourHood()
                if properties["harmonize_normals"]:
                    mesh.harmonizeNormals()
                    mesh.rebuildNeighbourHood()
                    applied.append("harmonize_normals")
                trace.append(
                    {
                        "operation": operation,
                        "applied": applied,
                        "before": before,
                        "after": _quick_counts(mesh),
                    }
                )
            else:
                diagnostics = mesh_diagnostics(mesh)
                requirements = _requirements(properties)
                _enforce_requirements(diagnostics, requirements)
                trace.append(
                    {
                        "operation": operation,
                        "requirements": requirements,
                        "result": diagnostics,
                    }
                )
    except MeshCandidateError:
        raise
    except Exception as exc:
        raise _fail(
            f"Native api.{operation} execution failed: {exc}",
            stage="native_operation",
            operation=operation,
            exception_type=type(exc).__name__,
        ) from exc
    if int(mesh.CountFacets) <= 0:
        raise _fail(
            f"Native api.{operation} removed every mesh facet.",
            stage="native_operation",
            operation=operation,
        )
    cache[digest] = mesh.copy(), [dict(item) for item in trace]
    return mesh, trace


def _export_mesh(mesh: Any, path: Path, *, output_name: str) -> str:
    try:
        mesh.write(str(path))
    except Exception as exc:
        raise _fail(
            f"Could not export Mesh output {output_name!r}: {exc}",
            stage="artifact_export",
            output_name=output_name,
            exception_type=type(exc).__name__,
        ) from exc
    if not path.is_file() or path.stat().st_size <= 0:
        raise _fail(
            f"Could not export Mesh output {output_name!r}.",
            stage="artifact_export",
            output_name=output_name,
        )
    return _sha256_file(path)


def validate_and_build_meshes(
    raw_result: Mapping[str, Any],
    expected_outputs: Sequence[Mapping[str, Any]],
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate, diagnose, and export every declared native Mesh output."""

    expected_names = [str(item.get("name") or "") for item in expected_outputs]
    if list(raw_result) != expected_names:
        raise _fail(
            "Mesh result keys must exactly match expected_outputs in declared order.",
            stage="result_contract",
            expected_names=expected_names,
            received_names=list(raw_result),
        )
    cache: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
    outputs = []
    summaries = []
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        if str(expected.get("type") or "") != "mesh":
            raise _fail(
                f"Mesh output {name!r} must declare type 'mesh'.",
                stage="result_contract",
                output_name=name,
            )
        definition = validate_mesh_definition(
            raw_result[name],
            require_domain_value=True,
            context=f"result[{name!r}]",
        )
        mesh, trace = _evaluate_definition(definition, cache)
        diagnostics = (
            dict(trace[-1]["result"])
            if definition["operation"] == "diagnostics"
            else mesh_diagnostics(mesh)
        )
        relative = Path("outputs") / f"output-{index:03d}.bms"
        artifact_sha256 = _export_mesh(mesh, root / relative, output_name=name)
        data = {
            "schema": VALIDATION_SCHEMA,
            "operation": str(definition["operation"]),
            "label": str(dict(definition["properties"]).get("label") or name),
            "artifact_sha256": artifact_sha256,
            "operation_trace": trace,
            "diagnostics": diagnostics,
        }
        outputs.append(
            {
                "name": name,
                "type": "mesh",
                "definition": definition,
                "artifact_kind": "mesh_bms",
                "artifact_path": str(relative),
                "mesh_data": data,
            }
        )
        summaries.append(
            {
                "name": name,
                "operation": data["operation"],
                "artifact_sha256": artifact_sha256,
                "points": diagnostics["points"],
                "facets": diagnostics["facets"],
                "open_edges": diagnostics["open_edges"],
                "components": diagnostics["components"],
                "is_solid": diagnostics["is_solid"],
            }
        )
    return outputs, {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "total_points": sum(item["points"] for item in summaries),
        "total_facets": sum(item["facets"] for item in summaries),
        "outputs": summaries,
    }
