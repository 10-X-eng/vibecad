# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cached, cancellable, process-isolated Mesh segmentation analysis."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Any, Mapping

from VibeCADIsolatedMeshWorker import freecadcmd_path, run_isolated_mesh_worker
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshSegments import (
    PreparedMeshSegment,
    accept_background_mesh_segment,
)


CACHE_SCHEMA = "vibecad-mesh-segmentation-cache-v1"
REQUEST_SCHEMA = "vibecad-mesh-segmentation-job-v1"
RESULT_SCHEMA = "vibecad-mesh-segmentation-result-v1"
ANALYSIS_SCHEMA = "vibecad-mesh-segmentation-analysis-v1"
TIMEOUT_SECONDS = 86_400
MAX_ANALYSIS_BYTES = 512 * 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MeshSegmentationRequest:
    captured: PreparedMeshSegment
    detached_meshes: tuple[Any, ...]
    cache_root: str
    freecadcmd: str
    child_script: str


@dataclass(frozen=True, slots=True)
class PreparedMeshSegmentationResult:
    request: MeshSegmentationRequest
    prepared: PreparedMeshSegment
    cache_key: str
    cache_hit: bool
    accepted_meshes: tuple[Any, ...]
    accepted_shapes: tuple[Any, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_root() -> Path:
    import FreeCAD as App

    return Path(str(App.getUserAppDataDir())) / "VibeCAD" / "cache" / CACHE_SCHEMA


def segmentation_cache_key(request: MeshSegmentationRequest) -> str:
    payload = {
        "schema": CACHE_SCHEMA,
        "operation": request.captured.operation,
        "sources": [
            target.source_geometry_sha256 for target in request.captured.targets
        ],
        "settings": dict(request.captured.settings),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _paths(request: MeshSegmentationRequest, key: str) -> tuple[Path, Path, Path]:
    directory = Path(request.cache_root) / key[:2] / key
    return directory / "analysis.json", directory / "metadata.json", directory / "artifacts"


def _read_analysis(path: Path, expected_sha256: str) -> list[Any] | None:
    try:
        size = path.stat().st_size
        if (
            not 1 <= size <= MAX_ANALYSIS_BYTES
            or _sha256(path) != expected_sha256
        ):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != ANALYSIS_SCHEMA
        or not isinstance(payload.get("analyses"), list)
    ):
        return None
    return payload["analyses"]


def _artifact_files(
    directory: Path,
    manifest: Any,
) -> tuple[tuple[Mapping[str, Any], Path, Path | None], ...] | None:
    if not isinstance(manifest, list):
        return None
    files = []
    names: set[str] = set()
    total_bytes = 0
    for item in manifest:
        if not isinstance(item, Mapping):
            return None
        name = str(item.get("name") or "")
        kind = str(item.get("kind") or "")
        if (
            not name
            or Path(name).name != name
            or name in names
            or kind not in {"mesh", "shape"}
            or not isinstance(item.get("sha256"), str)
        ):
            return None
        path = directory / name
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if (
            not 1 <= size <= MAX_ARTIFACT_BYTES
            or size != int(item.get("bytes", -1))
            or _sha256(path) != item["sha256"]
        ):
            return None
        total_bytes += size
        if total_bytes > MAX_ARTIFACT_BYTES:
            return None
        names.add(name)
        segments_path = None
        if kind == "mesh":
            segments_name = str(item.get("segments_name") or "")
            if (
                not segments_name
                or Path(segments_name).name != segments_name
                or segments_name in names
                or segments_name == name
                or not isinstance(item.get("segments_sha256"), str)
            ):
                return None
            segments_path = directory / segments_name
            try:
                segments_size = segments_path.stat().st_size
            except OSError:
                return None
            if (
                not 1 <= segments_size <= MAX_ARTIFACT_BYTES
                or segments_size != int(item.get("segments_bytes", -1))
                or _sha256(segments_path) != item["segments_sha256"]
            ):
                return None
            total_bytes += segments_size
            if total_bytes > MAX_ARTIFACT_BYTES:
                return None
            names.add(segments_name)
        files.append((item, path, segments_path))
    return tuple(files)


def _load_segments(path: Path, facet_count: int, expected_count: int) -> list[list[int]] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, list) or len(raw) != expected_count:
        return None
    segments = []
    for raw_segment in raw:
        if (
            not isinstance(raw_segment, list)
            or any(
                type(value) is not int or not 0 <= value < facet_count
                for value in raw_segment
            )
        ):
            return None
        segments.append(raw_segment)
    return segments


def _load_artifacts(
    directory: Path,
    manifest: Any,
) -> tuple[tuple[Any, ...], tuple[Any, ...]] | None:
    files = _artifact_files(directory, manifest)
    if files is None:
        return None
    meshes = []
    shapes = []
    try:
        for item, path, segments_path in files:
            if item["kind"] == "mesh":
                import Mesh

                mesh = Mesh.Mesh(str(path))
                if (
                    int(mesh.CountPoints) != int(item.get("points", -1))
                    or int(mesh.CountFacets) != int(item.get("facets", -1))
                    or int(mesh.CountFacets) < 1
                ):
                    return None
                if segments_path is None:
                    return None
                segments = _load_segments(
                    segments_path,
                    int(mesh.CountFacets),
                    int(item.get("segments", -1)),
                )
                if segments is None:
                    return None
                imported = [
                    [int(value) for value in mesh.getSegment(index)]
                    for index in range(int(mesh.countSegments()))
                ]
                if imported and imported != segments:
                    return None
                if not imported:
                    for segment in segments:
                        mesh.addSegment(segment)
                if int(mesh.countSegments()) != len(segments):
                    return None
                meshes.append(mesh)
            else:
                import Part

                shape = Part.Shape()
                shape.importBrep(str(path))
                if (
                    shape.isNull()
                    or not shape.isValid()
                    or len(shape.Edges) != int(item.get("edges", -1))
                    or len(shape.Faces) != int(item.get("faces", -1))
                    or len(shape.Solids) != int(item.get("solids", -1))
                ):
                    return None
                shapes.append(shape)
    except Exception:
        return None
    return tuple(meshes), tuple(shapes)


def _prepared(
    request: MeshSegmentationRequest,
    key: str,
    analysis_path: Path,
    artifact_directory: Path,
    metadata: Mapping[str, Any],
    *,
    hit: bool,
) -> PreparedMeshSegmentationResult | None:
    analyses = _read_analysis(analysis_path, str(metadata.get("analysis_sha256") or ""))
    if analyses is None:
        return None
    loaded = _load_artifacts(artifact_directory, metadata.get("artifacts"))
    if loaded is None:
        return None
    meshes, shapes = loaded
    prepared = accept_background_mesh_segment(request.captured, analyses)
    operation = prepared.operation
    expected_shapes = (
        sum(1 for output in prepared.outputs if output.kind != "Unused facets")
        if operation == "reverse_segmentation"
        and bool(prepared.settings["create_boundary_faces"])
        else len(prepared.targets) if operation == "mesh_boundary" else 0
    )
    expected_meshes = 1 if operation == "merge" else len(prepared.outputs)
    if len(meshes) != expected_meshes or len(shapes) != expected_shapes:
        return None
    if operation not in {"merge", "mesh_boundary"} and any(
        int(mesh.CountFacets) != len(output.facet_indices)
        for mesh, output in zip(meshes, prepared.outputs)
    ):
        return None
    prepared = replace(
        prepared,
        accepted_meshes=meshes,
        accepted_shapes=shapes,
    )
    return PreparedMeshSegmentationResult(
        request=request,
        prepared=prepared,
        cache_key=key,
        cache_hit=hit,
        accepted_meshes=meshes,
        accepted_shapes=shapes,
    )


def _cached(
    request: MeshSegmentationRequest,
    key: str,
) -> PreparedMeshSegmentationResult | None:
    analysis_path, metadata_path, artifact_directory = _paths(request, key)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema") != CACHE_SCHEMA
        or metadata.get("cache_key") != key
        or not isinstance(metadata.get("analysis_sha256"), str)
        or not isinstance(metadata.get("artifacts"), list)
    ):
        return None
    return _prepared(
        request,
        key,
        analysis_path,
        artifact_directory,
        metadata,
        hit=True,
    )


def _publish(
    request: MeshSegmentationRequest,
    key: str,
    source: Path,
    source_artifact_directory: Path,
    result: Mapping[str, Any],
) -> PreparedMeshSegmentationResult:
    size = source.stat().st_size
    digest = _sha256(source)
    if (
        not 1 <= size <= MAX_ANALYSIS_BYTES
        or digest != result.get("analysis_sha256")
        or size != int(result.get("analysis_bytes", -1))
    ):
        raise NativeMeshError(
            "The isolated Mesh segmentation artifact failed authentication.",
            error_code="NATIVE_MESH_SEGMENTATION_ARTIFACT_INVALID",
        )
    source_artifacts = _artifact_files(
        source_artifact_directory,
        result.get("artifacts"),
    )
    if source_artifacts is None:
        raise NativeMeshError(
            "The isolated Mesh geometry artifacts failed authentication.",
            error_code="NATIVE_MESH_SEGMENTATION_ARTIFACT_INVALID",
        )
    analysis_path, metadata_path, artifact_directory = _paths(request, key)
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    analysis_temp = analysis_path.with_name(
        f".{analysis_path.name}.{os.getpid()}.{token}.tmp"
    )
    metadata_temp = metadata_path.with_name(
        f".{metadata_path.name}.{os.getpid()}.{token}.tmp"
    )
    metadata = {
        "schema": CACHE_SCHEMA,
        "cache_key": key,
        "analysis_sha256": digest,
        "artifacts": [dict(item) for item, _path, _segments in source_artifacts],
    }
    try:
        with source.open("rb") as input_stream, analysis_temp.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(analysis_temp, analysis_path)
        for item, artifact_source, segments_source in source_artifacts:
            files_to_copy = [(artifact_source, str(item["name"]))]
            if segments_source is not None:
                files_to_copy.append((segments_source, str(item["segments_name"])))
            for source_path, name in files_to_copy:
                artifact = artifact_directory / name
                artifact_temp = artifact.with_name(
                    f".{artifact.name}.{os.getpid()}.{token}.tmp"
                )
                try:
                    with source_path.open("rb") as input_stream, artifact_temp.open(
                        "wb"
                    ) as output_stream:
                        shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                        output_stream.flush()
                        os.fsync(output_stream.fileno())
                    os.replace(artifact_temp, artifact)
                finally:
                    artifact_temp.unlink(missing_ok=True)
        with metadata_temp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(metadata_temp, metadata_path)
    finally:
        analysis_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)
    prepared = _prepared(
        request,
        key,
        analysis_path,
        artifact_directory,
        metadata,
        hit=False,
    )
    if prepared is None:
        raise NativeMeshError(
            "The isolated Mesh segmentation artifact failed authentication.",
            error_code="NATIVE_MESH_SEGMENTATION_ARTIFACT_INVALID",
        )
    return prepared


def run_mesh_segmentation(
    request: MeshSegmentationRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedMeshSegmentationResult:
    if not isinstance(request, MeshSegmentationRequest):
        raise TypeError("request must be a MeshSegmentationRequest")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(1, "Capturing exact Mesh snapshots")
    from VibeCADNativeMeshTargets import (
        rebind_prepared_mesh_targets,
        snapshot_mesh_targets,
    )

    exact_targets, snapshots = snapshot_mesh_targets(request.captured.targets)
    request = replace(
        request,
        captured=rebind_prepared_mesh_targets(request.captured, exact_targets),
        detached_meshes=snapshots,
    )
    key = segmentation_cache_key(request)
    cached = _cached(request, key)
    if cached is not None:
        progress(85, "Reusing verified Mesh segmentation")
        return cached
    if cancelled():
        raise NativeBackgroundCancelled()
    with tempfile.TemporaryDirectory(prefix="vibecad-mesh-segmentation-") as directory:
        root = Path(directory)
        request_path = root / "request.json"
        result_path = root / "result.json"
        analysis_path = root / "analysis.json"
        artifact_directory = root / "artifacts"
        source_paths = []
        source_segments_paths = []
        progress(4, "Writing detached Mesh snapshots")
        for index, mesh in enumerate(request.detached_meshes):
            source_path = root / f"source-{index:03d}.bms"
            source_segments_path = root / f"source-{index:03d}-segments.json"
            mesh.write(str(source_path))
            source_segments_path.write_text(
                json.dumps(
                    [
                        [int(value) for value in mesh.getSegment(segment_index)]
                        for segment_index in range(int(mesh.countSegments()))
                    ],
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            source_paths.append(str(source_path))
            source_segments_paths.append(str(source_segments_path))
        if cancelled():
            raise NativeBackgroundCancelled()
        request_path.write_text(
            json.dumps(
                {
                    "schema": REQUEST_SCHEMA,
                    "workspace": str(root),
                    "result_path": str(result_path),
                    "analysis_path": str(analysis_path),
                    "artifact_directory": str(artifact_directory),
                    "source_paths": source_paths,
                    "source_segments_paths": source_segments_paths,
                    "operation": request.captured.operation,
                    "settings": dict(request.captured.settings),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        progress(10, "Analyzing Mesh in isolated worker")
        result = run_isolated_mesh_worker(
            freecadcmd=request.freecadcmd,
            child_script=request.child_script,
            request_path=request_path,
            result_path=result_path,
            expected_schema=RESULT_SCHEMA,
            cancelled=cancelled,
            timeout_seconds=TIMEOUT_SECONDS,
            failure_code="NATIVE_MESH_SEGMENTATION_FAILED",
        )
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(85, "Authenticating Mesh segmentation")
        return _publish(
            request,
            key,
            analysis_path,
            artifact_directory,
            result,
        )


def make_request(captured: PreparedMeshSegment) -> MeshSegmentationRequest:
    if not isinstance(captured, PreparedMeshSegment):
        raise TypeError("captured must be a PreparedMeshSegment")
    return MeshSegmentationRequest(
        captured=captured,
        detached_meshes=tuple(target.source_mesh for target in captured.targets),
        cache_root=str(cache_root()),
        freecadcmd=str(freecadcmd_path()),
        child_script=str(Path(__file__).resolve().with_name("VibeCADMeshSegmentationChild.py")),
    )
