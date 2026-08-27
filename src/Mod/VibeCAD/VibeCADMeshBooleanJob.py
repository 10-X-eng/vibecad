# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cached, cancellable, process-isolated solid Mesh boolean preparation."""

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
from VibeCADMeshCacheAtomic import atomic_cache_temporary_path
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeMeshErrors import NativeMeshError


CACHE_SCHEMA = "vibecad-mesh-boolean-cache-v1"
REQUEST_SCHEMA = "vibecad-mesh-boolean-job-v1"
RESULT_SCHEMA = "vibecad-mesh-boolean-result-v1"
TIMEOUT_SECONDS = 86_400
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MeshBooleanRequest:
    operation: str
    first: Any
    second: Any
    first_mesh: Any
    second_mesh: Any
    first_placement: Mapping[str, Any]
    second_placement: Mapping[str, Any]
    result_label: str
    linear_deflection_mm: float
    angular_deflection_radians: float
    relative: bool
    cache_root: str
    freecadcmd: str
    child_script: str


@dataclass(frozen=True, slots=True)
class PreparedMeshBooleanResult:
    request: MeshBooleanRequest
    artifact_path: str
    artifact_sha256: str
    cache_key: str
    cache_hit: bool
    points: int
    facets: int
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


def cache_key(request: MeshBooleanRequest) -> str:
    payload = {
        "schema": CACHE_SCHEMA,
        "operation": request.operation,
        "first_geometry_sha256": str(request.first.source_geometry_sha256),
        "second_geometry_sha256": str(request.second.source_geometry_sha256),
        "first_placement": dict(request.first_placement),
        "second_placement": dict(request.second_placement),
        "linear_deflection_mm": request.linear_deflection_mm,
        "angular_deflection_radians": request.angular_deflection_radians,
        "relative": request.relative,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _paths(request: MeshBooleanRequest, key: str) -> tuple[Path, Path]:
    directory = Path(request.cache_root) / key[:2] / key
    return directory / "result.bms", directory / "metadata.json"


def _prepared(
    request: MeshBooleanRequest,
    artifact: Path,
    metadata: Mapping[str, Any],
    *,
    hit: bool,
) -> PreparedMeshBooleanResult:
    return PreparedMeshBooleanResult(
        request=request,
        artifact_path=str(artifact),
        artifact_sha256=str(metadata["artifact_sha256"]),
        cache_key=str(metadata["cache_key"]),
        cache_hit=hit,
        points=int(metadata["points"]),
        facets=int(metadata["facets"]),
        bounds_mm=dict(metadata["bounds_mm"]),
    )


def _cached(request: MeshBooleanRequest, key: str) -> PreparedMeshBooleanResult | None:
    artifact, metadata_path = _paths(request, key)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        size = artifact.stat().st_size
    except (OSError, ValueError):
        return None
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema") != CACHE_SCHEMA
        or metadata.get("cache_key") != key
        or not 1 <= size <= MAX_ARTIFACT_BYTES
        or metadata.get("artifact_sha256") != _sha256(artifact)
        or int(metadata.get("points", 0)) < 4
        or int(metadata.get("facets", 0)) < 4
        or not isinstance(metadata.get("bounds_mm"), dict)
    ):
        return None
    return _prepared(request, artifact, metadata, hit=True)


def _publish(
    request: MeshBooleanRequest,
    key: str,
    source: Path,
    result: Mapping[str, Any],
) -> PreparedMeshBooleanResult:
    size = source.stat().st_size
    if not 1 <= size <= MAX_ARTIFACT_BYTES:
        raise NativeMeshError(
            "The isolated Mesh boolean artifact is empty or exceeds 16 GiB.",
            error_code="NATIVE_MESH_BOOLEAN_ARTIFACT_INVALID",
        )
    digest = _sha256(source)
    artifact, metadata_path = _paths(request, key)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    artifact_temp = atomic_cache_temporary_path(
        artifact.parent, role="mesh-artifact", token=token
    )
    metadata_temp = atomic_cache_temporary_path(
        metadata_path.parent, role="metadata", token=token
    )
    metadata = {
        "schema": CACHE_SCHEMA,
        "cache_key": key,
        "artifact_sha256": digest,
        "points": int(result["points"]),
        "facets": int(result["facets"]),
        "bounds_mm": dict(result["bounds_mm"]),
    }
    try:
        with source.open("rb") as input_stream, artifact_temp.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(artifact_temp, artifact)
        with metadata_temp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(metadata_temp, metadata_path)
    finally:
        artifact_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)
    return _prepared(request, artifact, metadata, hit=False)


def run_mesh_boolean(
    request: MeshBooleanRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedMeshBooleanResult:
    if not isinstance(request, MeshBooleanRequest):
        raise TypeError("request must be a MeshBooleanRequest")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(1, "Capturing exact Mesh snapshots")
    from VibeCADNativeMeshTargets import snapshot_mesh_targets

    exact_targets, snapshots = snapshot_mesh_targets((request.first, request.second))

    def world_mesh(mesh: Any) -> Any:
        placement = mesh.Placement
        mesh.Placement = type(placement)()
        mesh.transform(placement.toMatrix())
        return mesh

    request = replace(
        request,
        first=exact_targets[0],
        second=exact_targets[1],
        first_mesh=world_mesh(snapshots[0]),
        second_mesh=world_mesh(snapshots[1]),
    )
    key = cache_key(request)
    cached = _cached(request, key)
    if cached is not None:
        progress(85, "Reusing verified Mesh boolean")
        return cached
    if cancelled():
        raise NativeBackgroundCancelled()
    with tempfile.TemporaryDirectory(prefix="vibecad-mesh-boolean-") as directory:
        root = Path(directory)
        first_path = root / "first.bms"
        second_path = root / "second.bms"
        output_path = root / "result.bms"
        request_path = root / "request.json"
        result_path = root / "result.json"
        progress(4, "Writing detached Mesh snapshots")
        request.first_mesh.write(str(first_path))
        request.second_mesh.write(str(second_path))
        if cancelled():
            raise NativeBackgroundCancelled()
        request_path.write_text(
            json.dumps(
                {
                    "schema": REQUEST_SCHEMA,
                    "workspace": str(root),
                    "first_path": str(first_path),
                    "second_path": str(second_path),
                    "output_path": str(output_path),
                    "result_path": str(result_path),
                    "operation": request.operation,
                    "linear_deflection_mm": request.linear_deflection_mm,
                    "angular_deflection_radians": request.angular_deflection_radians,
                    "relative": request.relative,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        progress(10, "Computing solid boolean in isolated worker")
        result = run_isolated_mesh_worker(
            freecadcmd=request.freecadcmd,
            child_script=request.child_script,
            request_path=request_path,
            result_path=result_path,
            expected_schema=RESULT_SCHEMA,
            cancelled=cancelled,
            timeout_seconds=TIMEOUT_SECONDS,
            failure_code="NATIVE_MESH_BOOLEAN_FAILED",
        )
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(85, "Authenticating Mesh boolean result")
        return _publish(request, key, output_path, result)


def make_request(
    *,
    operation: str,
    first: Any,
    second: Any,
    first_mesh: Any,
    second_mesh: Any,
    first_placement: Mapping[str, Any],
    second_placement: Mapping[str, Any],
    result_label: str,
    linear_deflection_mm: float,
    angular_deflection_radians: float,
    relative: bool,
) -> MeshBooleanRequest:
    return MeshBooleanRequest(
        operation=operation,
        first=first,
        second=second,
        first_mesh=first_mesh,
        second_mesh=second_mesh,
        first_placement=dict(first_placement),
        second_placement=dict(second_placement),
        result_label=result_label,
        linear_deflection_mm=linear_deflection_mm,
        angular_deflection_radians=angular_deflection_radians,
        relative=relative,
        cache_root=str(cache_root()),
        freecadcmd=str(freecadcmd_path()),
        child_script=str(Path(__file__).resolve().with_name("VibeCADMeshBooleanChild.py")),
    )
