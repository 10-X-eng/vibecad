# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cached, cancellable, process-isolated shape tessellation."""

from __future__ import annotations

from dataclasses import dataclass
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


CACHE_SCHEMA = "vibecad-shape-tessellation-cache-v1"
REQUEST_SCHEMA = "vibecad-shape-tessellation-job-v1"
RESULT_SCHEMA = "vibecad-shape-tessellation-result-v1"
TIMEOUT_SECONDS = 86_400
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ShapeTessellationRequest:
    source: Any
    subelements: tuple[str, ...]
    source_shape: Any
    source_signature: Mapping[str, Any]
    label: str
    settings: Mapping[str, Any]
    cache_root: str
    freecadcmd: str
    child_script: str


@dataclass(frozen=True, slots=True)
class PreparedShapeTessellation:
    request: ShapeTessellationRequest
    mesh: Any
    artifact_path: str
    artifact_sha256: str
    segments_path: str
    segments_sha256: str
    source_brep_sha256: str
    cache_key: str
    cache_hit: bool
    points: int
    facets: int
    segments: int
    bounds_mm: Mapping[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_root() -> Path:
    import FreeCAD as App

    return Path(str(App.getUserAppDataDir())) / "VibeCAD" / "cache" / CACHE_SCHEMA


def _cache_key(
    source_brep_sha256: str,
    subelements: tuple[str, ...],
    settings: Mapping[str, Any],
) -> str:
    payload = {
        "schema": CACHE_SCHEMA,
        "source_brep_sha256": source_brep_sha256,
        "subelements": list(subelements),
        "settings": dict(settings),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _paths(request: ShapeTessellationRequest, key: str) -> tuple[Path, Path]:
    directory = Path(request.cache_root) / key[:2] / key
    return directory, directory / "metadata.json"


def _artifact_paths(
    directory: Path,
    artifact_sha256: str,
    segments_sha256: str,
) -> tuple[Path, Path]:
    return (
        directory / f"mesh-{artifact_sha256}.bms",
        directory / f"segments-{segments_sha256}.json",
    )


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _segments(path: Path, facet_count: int, expected_count: int) -> list[list[int]]:
    try:
        if not 1 <= path.stat().st_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("segment artifact size is outside its bound")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NativeMeshError(
            "The cached tessellation segments are invalid.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        ) from exc
    if not isinstance(raw, list) or len(raw) != expected_count:
        raise NativeMeshError(
            "The cached tessellation segment count is invalid.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )
    segments = []
    for raw_segment in raw:
        if not isinstance(raw_segment, list):
            raise NativeMeshError(
                "A cached tessellation segment is invalid.",
                error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
            )
        segment = [int(value) for value in raw_segment if type(value) is int]
        if len(segment) != len(raw_segment) or any(not 0 <= value < facet_count for value in segment):
            raise NativeMeshError(
                "A cached tessellation segment contains an invalid facet index.",
                error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
            )
        segments.append(segment)
    return segments


def _prepared(
    request: ShapeTessellationRequest,
    artifact: Path,
    segments_path: Path,
    metadata: Mapping[str, Any],
    *,
    hit: bool,
) -> PreparedShapeTessellation:
    if _sha256(artifact) != str(metadata["artifact_sha256"]):
        raise NativeMeshError(
            "The cached tessellation Mesh failed authentication.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )
    if _sha256(segments_path) != str(metadata["segments_sha256"]):
        raise NativeMeshError(
            "The cached tessellation segments failed authentication.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )
    import Mesh

    mesh = Mesh.Mesh(str(artifact))
    if int(mesh.CountPoints) != int(metadata["points"]) or int(mesh.CountFacets) != int(
        metadata["facets"]
    ):
        raise NativeMeshError(
            "The cached tessellation Mesh does not match its metadata.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )
    segments = _segments(segments_path, int(mesh.CountFacets), int(metadata["segments"]))
    imported = [
        [int(value) for value in mesh.getSegment(index)]
        for index in range(int(mesh.countSegments()))
    ]
    if imported and imported != segments:
        raise NativeMeshError(
            "The cached tessellation Mesh has conflicting segment data.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )
    if not imported:
        for segment in segments:
            mesh.addSegment(segment)
    if int(mesh.countSegments()) != int(metadata["segments"]):
        raise NativeMeshError(
            "The cached tessellation segment data could not be restored.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )
    return PreparedShapeTessellation(
        request=request,
        mesh=mesh,
        artifact_path=str(artifact),
        artifact_sha256=str(metadata["artifact_sha256"]),
        segments_path=str(segments_path),
        segments_sha256=str(metadata["segments_sha256"]),
        source_brep_sha256=str(metadata["source_brep_sha256"]),
        cache_key=str(metadata["cache_key"]),
        cache_hit=hit,
        points=int(metadata["points"]),
        facets=int(metadata["facets"]),
        segments=int(metadata["segments"]),
        bounds_mm=dict(metadata["bounds_mm"]),
    )


def _cached(
    request: ShapeTessellationRequest,
    key: str,
    source_brep_sha256: str,
) -> PreparedShapeTessellation | None:
    directory, metadata_path = _paths(request, key)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            return None
        artifact_digest = metadata.get("artifact_sha256")
        segments_digest = metadata.get("segments_sha256")
        if not _valid_digest(artifact_digest) or not _valid_digest(segments_digest):
            return None
        artifact, segments_path = _artifact_paths(
            directory,
            artifact_digest,
            segments_digest,
        )
        size = artifact.stat().st_size
        segment_size = segments_path.stat().st_size
    except (OSError, ValueError):
        return None
    if (
        metadata.get("schema") != CACHE_SCHEMA
        or metadata.get("cache_key") != key
        or metadata.get("source_brep_sha256") != source_brep_sha256
        or not 1 <= size <= MAX_ARTIFACT_BYTES
        or not 1 <= segment_size <= MAX_ARTIFACT_BYTES
        or int(metadata.get("points", 0)) < 1
        or int(metadata.get("facets", 0)) < 1
        or int(metadata.get("segments", 0)) < 0
        or not isinstance(metadata.get("bounds_mm"), dict)
    ):
        return None
    try:
        return _prepared(request, artifact, segments_path, metadata, hit=True)
    except NativeMeshError:
        return None


def _publish(
    request: ShapeTessellationRequest,
    key: str,
    source_brep_sha256: str,
    source: Path,
    segment_source: Path,
    result: Mapping[str, Any],
) -> PreparedShapeTessellation:
    size = source.stat().st_size
    if not 1 <= size <= MAX_ARTIFACT_BYTES:
        raise NativeMeshError(
            "The isolated shape tessellation artifact is empty or exceeds 16 GiB.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )
    digest = _sha256(source)
    if not segment_source.is_file() or not 1 <= segment_source.stat().st_size <= MAX_ARTIFACT_BYTES:
        raise NativeMeshError(
            "The isolated tessellation segment artifact is invalid.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )
    segment_digest = _sha256(segment_source)
    directory, metadata_path = _paths(request, key)
    directory.mkdir(parents=True, exist_ok=True)
    artifact, segments_path = _artifact_paths(directory, digest, segment_digest)
    token = secrets.token_hex(8)
    artifact_temp = artifact.with_name(f".{artifact.name}.{os.getpid()}.{token}.tmp")
    segments_temp = segments_path.with_name(f".{segments_path.name}.{os.getpid()}.{token}.tmp")
    metadata_temp = metadata_path.with_name(
        f".{metadata_path.name}.{os.getpid()}.{token}.tmp"
    )
    metadata = {
        "schema": CACHE_SCHEMA,
        "cache_key": key,
        "source_brep_sha256": source_brep_sha256,
        "artifact_sha256": digest,
        "segments_sha256": segment_digest,
        "points": int(result["points"]),
        "facets": int(result["facets"]),
        "segments": int(result["segments"]),
        "bounds_mm": dict(result["bounds_mm"]),
    }
    try:
        with source.open("rb") as input_stream, artifact_temp.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(artifact_temp, artifact)
        with segment_source.open("rb") as input_stream, segments_temp.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(segments_temp, segments_path)
        with metadata_temp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(metadata_temp, metadata_path)
    finally:
        artifact_temp.unlink(missing_ok=True)
        segments_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)
    return _prepared(request, artifact, segments_path, metadata, hit=False)


def run_shape_tessellation(
    request: ShapeTessellationRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedShapeTessellation:
    if not isinstance(request, ShapeTessellationRequest):
        raise TypeError("request must be a ShapeTessellationRequest")
    if cancelled():
        raise NativeBackgroundCancelled()
    with tempfile.TemporaryDirectory(prefix="vibecad-shape-tessellation-") as directory:
        root = Path(directory)
        source_path = root / "source.brep"
        output_path = root / "mesh.bms"
        segments_path = root / "segments.json"
        request_path = root / "request.json"
        result_path = root / "result.json"
        progress(3, "Writing detached shape snapshot")
        try:
            request.source_shape.exportBrep(str(source_path))
        except Exception as exc:
            raise NativeMeshError(
                "The detached shape snapshot could not be written.",
                error_code="NATIVE_MESH_TESSELLATION_SOURCE_INVALID",
            ) from exc
        if not source_path.is_file() or source_path.stat().st_size < 1:
            raise NativeMeshError(
                "The detached shape snapshot is empty.",
                error_code="NATIVE_MESH_TESSELLATION_SOURCE_INVALID",
            )
        source_digest = _sha256(source_path)
        key = _cache_key(source_digest, request.subelements, request.settings)
        cached = _cached(request, key, source_digest)
        if cached is not None:
            progress(85, "Reusing verified shape tessellation")
            return cached
        if cancelled():
            raise NativeBackgroundCancelled()
        request_path.write_text(
            json.dumps(
                {
                    "schema": REQUEST_SCHEMA,
                    "workspace": str(root),
                    "source_path": str(source_path),
                    "subelements": list(request.subelements),
                    "output_path": str(output_path),
                    "segments_path": str(segments_path),
                    "result_path": str(result_path),
                    "settings": dict(request.settings),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        progress(10, "Tessellating shape in isolated worker")
        result = run_isolated_mesh_worker(
            freecadcmd=request.freecadcmd,
            child_script=request.child_script,
            request_path=request_path,
            result_path=result_path,
            expected_schema=RESULT_SCHEMA,
            cancelled=cancelled,
            timeout_seconds=TIMEOUT_SECONDS,
            failure_code="NATIVE_MESH_TESSELLATION_FAILED",
        )
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(85, "Authenticating tessellated Mesh")
        return _publish(request, key, source_digest, output_path, segments_path, result)


def make_request(
    *,
    source: Any,
    subelements: tuple[str, ...],
    source_shape: Any,
    source_signature: Mapping[str, Any],
    label: str,
    settings: Mapping[str, Any],
) -> ShapeTessellationRequest:
    return ShapeTessellationRequest(
        source=source,
        subelements=tuple(subelements),
        source_shape=source_shape,
        source_signature=dict(source_signature),
        label=label,
        settings=dict(settings),
        cache_root=str(cache_root()),
        freecadcmd=str(freecadcmd_path()),
        child_script=str(Path(__file__).resolve().with_name("VibeCADMeshTessellationChild.py")),
    )
