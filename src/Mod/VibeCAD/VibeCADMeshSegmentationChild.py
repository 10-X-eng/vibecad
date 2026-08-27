# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd worker for Mesh component and surface analysis."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from VibeCADNativeMeshComponents import mesh_components


REQUEST_SCHEMA = "vibecad-mesh-segmentation-job-v1"
RESULT_SCHEMA = "vibecad-mesh-segmentation-result-v1"
ANALYSIS_SCHEMA = "vibecad-mesh-segmentation-analysis-v1"
OPERATIONS = frozenset(
    {
        "merge",
        "split_components",
        "mesh_segmentation",
        "segmentation_best_fit",
        "reverse_segmentation",
        "segmentation_manual",
        "segmentation_from_components",
        "mesh_boundary",
    }
)


class SegmentationFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


def _path(value: Any, root: Path, field: str) -> Path:
    path = Path(str(value or "")).resolve()
    if path.parent != root:
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_ARTIFACT_INVALID",
            f"{field} must be one file in the private segmentation workspace.",
        )
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _components(mesh: Any) -> list[dict[str, Any]]:
    components = mesh_components(mesh)
    if len(components) <= 1:
        return []
    return [
        {
            "kind": "Connected component",
            "facet_indices": list(component.facet_indices),
        }
        for component in components
    ]


def _typed_requests(values: Any, expected_fields: int) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(values, list):
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_REQUEST_INVALID",
            "Surface requests are invalid.",
        )
    requests = []
    for value in values:
        if not isinstance(value, list) or len(value) != expected_fields:
            raise SegmentationFailure(
                "NATIVE_MESH_SEGMENTATION_REQUEST_INVALID",
                "A surface request is invalid.",
            )
        parameters = value[-1]
        if isinstance(parameters, list):
            parameters = tuple(parameters)
        requests.append((*value[:-1], parameters))
    return tuple(requests)


def _artifact_directory(value: Any, root: Path) -> Path:
    directory = Path(str(value or "")).resolve()
    if directory.parent != root:
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_ARTIFACT_INVALID",
            "artifact_directory must be one directory in the private workspace.",
        )
    directory.mkdir(parents=False, exist_ok=False)
    return directory


def _artifact(path: Path, kind: str, **metadata: Any) -> dict[str, Any]:
    size = path.stat().st_size
    if size < 1:
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_ARTIFACT_INVALID",
            "The isolated Mesh geometry artifact is empty.",
        )
    return {
        "name": path.name,
        "kind": kind,
        "bytes": size,
        "sha256": _sha256(path),
        **metadata,
    }


def _write_mesh(mesh: Any, directory: Path, index: int) -> dict[str, Any]:
    path = directory / f"mesh-{index:04d}.bms"
    segments_path = directory / f"mesh-{index:04d}-segments.json"
    mesh.write(str(path))
    segments = [
        [int(value) for value in mesh.getSegment(segment_index)]
        for segment_index in range(int(mesh.countSegments()))
    ]
    segments_path.write_text(
        json.dumps(segments, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return _artifact(
        path,
        "mesh",
        points=int(mesh.CountPoints),
        facets=int(mesh.CountFacets),
        segments=len(segments),
        segments_name=segments_path.name,
        segments_bytes=segments_path.stat().st_size,
        segments_sha256=_sha256(segments_path),
    )


def _restore_segments(mesh: Any, path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_SOURCE_INVALID",
            "A detached Mesh segment artifact is invalid.",
        ) from exc
    facet_count = int(mesh.CountFacets)
    if not isinstance(raw, list):
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_SOURCE_INVALID",
            "A detached Mesh segment artifact is invalid.",
        )
    segments = []
    for raw_segment in raw:
        if (
            not isinstance(raw_segment, list)
            or any(
                type(value) is not int or not 0 <= value < facet_count
                for value in raw_segment
            )
        ):
            raise SegmentationFailure(
                "NATIVE_MESH_SEGMENTATION_SOURCE_INVALID",
                "A detached Mesh segment contains an invalid facet index.",
            )
        segments.append(raw_segment)
    imported = [
        [int(value) for value in mesh.getSegment(index)]
        for index in range(int(mesh.countSegments()))
    ]
    if imported and imported != segments:
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_SOURCE_INVALID",
            "A detached Mesh contains conflicting segment data.",
        )
    if not imported:
        for segment in segments:
            mesh.addSegment(segment)


def _world_mesh(mesh: Any) -> Any:
    import FreeCAD as App

    result = mesh.copy()
    placement = result.Placement
    result.Placement = App.Placement()
    result.transform(placement.toMatrix())
    return result


def _merge_meshes(meshes: list[Any]) -> Any:
    import Mesh

    merged = Mesh.Mesh()
    for mesh in meshes:
        source = _world_mesh(mesh)
        facet_offset = int(merged.CountFacets)
        source_facets = int(source.CountFacets)
        merged.addMesh(source)
        segments = [
            [facet_offset + int(index) for index in source.getSegment(segment_index)]
            for segment_index in range(int(source.countSegments()))
        ]
        if not segments:
            segments = [list(range(facet_offset, facet_offset + source_facets))]
        for segment in segments:
            merged.addSegment(segment)
    if int(merged.CountFacets) < 1:
        raise SegmentationFailure(
            "NATIVE_MESH_MERGE_EMPTY",
            "The isolated Mesh merge produced no facets.",
        )
    return merged


def _mesh_subset(mesh: Any, facet_indices: list[int]) -> Any:
    """Extract exact facets and retain every intersecting source segment."""
    result = mesh.meshFromSegment(facet_indices)
    source_to_result = {
        int(source_index): result_index
        for result_index, source_index in enumerate(facet_indices)
    }
    for segment_index in range(int(mesh.countSegments())):
        retained = [
            source_to_result[int(source_index)]
            for source_index in mesh.getSegment(segment_index)
            if int(source_index) in source_to_result
        ]
        if retained:
            result.addSegment(retained)
    return result


def _boundary_artifact(
    mesh: Any,
    facet_indices: list[int],
    make_faces: bool,
    directory: Path,
    index: int,
) -> dict[str, Any]:
    import FreeCAD as App
    import MeshPart  # noqa: F401 - registers MeshPart::Boundary

    document = App.newDocument(f"VibeCADMeshBoundaryWorker{index}")
    try:
        source = document.addObject("Mesh::Feature", "Source")
        source.Mesh = mesh
        boundary = document.addObject("MeshPart::Boundary", "Boundary")
        boundary.Source = source
        boundary.FacetIndices = facet_indices
        if facet_indices:
            boundary.AcceptedTopology = mesh
        boundary.MakeFaces = make_faces
        if document.recompute([boundary], True, True) is False or not boundary.isValid():
            detail = str(boundary.getStatusString() or "").strip()
            raise SegmentationFailure(
                "NATIVE_MESH_BOUNDARY_FAILED",
                detail or "The isolated Mesh boundary failed.",
            )
        shape = boundary.Shape
        if shape.isNull() or not shape.isValid():
            raise SegmentationFailure(
                "NATIVE_MESH_BOUNDARY_FAILED",
                "The isolated Mesh boundary produced invalid geometry.",
            )
        path = directory / f"shape-{index:04d}.brep"
        shape.exportBrep(str(path))
        return _artifact(
            path,
            "shape",
            edges=len(shape.Edges),
            faces=len(shape.Faces),
            solids=len(shape.Solids),
        )
    finally:
        App.closeDocument(document.Name)


def _segment_indices(
    operation: str,
    analyses: list[list[dict[str, Any]]],
    meshes: list[Any],
    settings: Mapping[str, Any],
) -> list[tuple[int, list[int]]]:
    values = [
        (target_index, list(segment["facet_indices"]))
        for target_index, segments in enumerate(analyses)
        for segment in segments
    ]
    if operation == "reverse_segmentation" and bool(settings["include_unused_facets"]):
        used = {index for _target, indices in values for index in indices}
        unused = [index for index in range(int(meshes[0].CountFacets)) if index not in used]
        if unused:
            values.append((0, unused))
    return values


def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema") != REQUEST_SCHEMA:
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_REQUEST_INVALID",
            "The isolated Mesh segmentation request has an unsupported schema.",
        )
    root = Path(str(request.get("workspace") or "")).resolve()
    if not root.is_dir():
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_REQUEST_INVALID",
            "The isolated Mesh segmentation workspace is unavailable.",
        )
    operation = str(request.get("operation") or "")
    settings = request.get("settings")
    raw_sources = request.get("source_paths")
    raw_source_segments = request.get("source_segments_paths")
    if (
        operation not in OPERATIONS
        or not isinstance(settings, Mapping)
        or not isinstance(raw_sources, list)
        or not 1 <= len(raw_sources) <= 32
        or not isinstance(raw_source_segments, list)
        or len(raw_source_segments) != len(raw_sources)
    ):
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_REQUEST_INVALID",
            "The isolated Mesh segmentation request is incomplete.",
        )
    analysis_path = _path(request.get("analysis_path"), root, "analysis_path")
    artifact_directory = _artifact_directory(request.get("artifact_directory"), root)

    import Mesh

    meshes = [Mesh.Mesh(str(_path(value, root, "source_path"))) for value in raw_sources]
    for mesh, value in zip(meshes, raw_source_segments):
        _restore_segments(mesh, _path(value, root, "source_segments_path"))
    if any(int(mesh.CountFacets) < 1 for mesh in meshes):
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_SOURCE_EMPTY",
            "An isolated Mesh segmentation source contains no facets.",
        )
    artifacts: list[dict[str, Any]] = []
    if operation == "merge":
        analyses = []
        artifacts.append(_write_mesh(_merge_meshes(meshes), artifact_directory, 0))
    elif operation == "mesh_boundary":
        analyses = []
        for index, mesh in enumerate(meshes):
            artifacts.append(
                _boundary_artifact(
                    mesh,
                    [],
                    bool(settings["make_faces"]),
                    artifact_directory,
                    index,
                )
            )
    elif operation in {"split_components", "segmentation_from_components"}:
        analyses = [_components(mesh) for mesh in meshes]
    elif operation == "segmentation_manual":
        segments = settings.get("segments")
        if not isinstance(segments, list) or not 1 <= len(segments) <= 2:
            raise SegmentationFailure(
                "NATIVE_MESH_SEGMENTATION_REQUEST_INVALID",
                "Manual Mesh segments are invalid.",
            )
        analyses = [segments]
    elif len(meshes) != 1:
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_REQUEST_INVALID",
            "Surface segmentation requires one exact Mesh.",
        )
    elif operation == "mesh_segmentation":
        analyses = [
            Mesh.detectCurvatureSegments(
                meshes[0],
                _typed_requests(settings["surface_requests"], 3),
                int(settings["smoothing_steps"]),
            )
        ]
    elif operation == "segmentation_best_fit":
        analyses = [
            Mesh.detectBestFitSegments(
                meshes[0],
                _typed_requests(settings["surface_requests"], 4),
            )
        ]
    else:
        analyses = [
            Mesh.detectPlanarSegments(
                meshes[0],
                int(settings["minimum_facets"]),
                float(settings["curvature_tolerance"]),
                float(settings["distance_tolerance_mm"]),
                int(settings["smoothing_steps"]),
            )
        ]
    if operation not in {"merge", "mesh_boundary"}:
        segment_values = _segment_indices(operation, analyses, meshes, settings)
        for index, (target_index, facet_indices) in enumerate(segment_values):
            segment = _mesh_subset(meshes[target_index], facet_indices)
            if int(segment.CountFacets) != len(facet_indices):
                raise SegmentationFailure(
                    "NATIVE_MESH_SEGMENTATION_ARTIFACT_INVALID",
                    "A detached Mesh segment has the wrong facet count.",
                )
            artifacts.append(_write_mesh(segment, artifact_directory, index))
        if operation == "reverse_segmentation" and bool(settings["create_boundary_faces"]):
            shape_index = 0
            for target_index, facet_indices in segment_values:
                if len(facet_indices) == int(meshes[target_index].CountFacets) and not analyses[0]:
                    continue
                detected_indices = {
                    value
                    for segment in analyses[target_index]
                    for value in segment["facet_indices"]
                }
                if not set(facet_indices).issubset(detected_indices):
                    continue
                artifacts.append(
                    _boundary_artifact(
                        meshes[target_index],
                        facet_indices,
                        True,
                        artifact_directory,
                        shape_index,
                    )
                )
                shape_index += 1

    payload = {"schema": ANALYSIS_SCHEMA, "analyses": analyses}
    analysis_path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    size = analysis_path.stat().st_size
    if size < 1:
        raise SegmentationFailure(
            "NATIVE_MESH_SEGMENTATION_ARTIFACT_INVALID",
            "The isolated Mesh segmentation produced no analysis artifact.",
        )
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "analysis_sha256": _sha256(analysis_path),
        "analysis_bytes": size,
        "target_count": len(analyses),
        "segment_count": sum(len(value) for value in analyses),
        "artifacts": artifacts,
    }


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The Mesh segmentation request is not an object.")
    root = Path(str(request.get("workspace") or "")).resolve()
    result_path = _path(request.get("result_path"), root, "result_path")
    try:
        result = execute(request)
    except SegmentationFailure as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "failure_code": exc.code,
            "error": str(exc),
        }
    except Exception as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "failure_code": "NATIVE_MESH_SEGMENTATION_FAILED",
            "error": "The isolated Mesh segmentation failed.",
            "exception_type": type(exc).__name__,
            "native_error": str(exc)[:1000],
        }
    result_path.write_text(
        json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return 2
    result = run(arguments[-1])
    return 0 if result.get("ok") is True else 1


if __name__ != "VibeCADMeshSegmentationChild":
    raise SystemExit(main())
