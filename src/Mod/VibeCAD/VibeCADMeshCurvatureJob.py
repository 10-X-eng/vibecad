# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cached, cancellable, isolated preparation of Mesh curvature samples."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import struct
import tempfile
from typing import Any, Mapping

from VibeCADIsolatedMeshWorker import freecadcmd_path, run_isolated_mesh_worker
from VibeCADMeshCacheAtomic import atomic_cache_temporary_path
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeMeshErrors import NativeMeshError


CACHE_SCHEMA = "vibecad-mesh-curvature-cache-v1"
REQUEST_SCHEMA = "vibecad-mesh-curvature-job-v1"
RESULT_SCHEMA = "vibecad-mesh-curvature-result-v1"
MAGIC = b"VCURV01\0"
TIMEOUT_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class MeshCurvatureRequest:
    prepared: Any
    detached_meshes: tuple[Any, ...]
    cache_root: str
    freecadcmd: str
    child_script: str


@dataclass(frozen=True, slots=True)
class PreparedMeshCurvatureResult:
    request: MeshCurvatureRequest
    prepared: Any
    artifacts: tuple[bytes, ...]
    cache_key: str
    cache_hit: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_root() -> Path:
    import FreeCAD as App

    return Path(str(App.getUserAppDataDir())) / "VibeCAD" / "cache" / CACHE_SCHEMA


def cache_key(request: MeshCurvatureRequest) -> str:
    payload = {
        "schema": CACHE_SCHEMA,
        "sources": [
            target.source_geometry_sha256 for target in request.prepared.targets
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _paths(request: MeshCurvatureRequest, key: str) -> tuple[Path, tuple[Path, ...]]:
    directory = Path(request.cache_root) / key[:2] / key
    return directory / "metadata.json", tuple(
        directory / f"result-{index:03d}.vcurv"
        for index in range(len(request.prepared.targets))
    )


def _read_artifact(path: Path, entry: Mapping[str, Any], expected_count: int) -> bytes | None:
    expected_size = 16 + expected_count * 32
    try:
        if (
            path.stat().st_size != expected_size
            or int(entry.get("bytes", -1)) != expected_size
            or int(entry.get("samples", -1)) != expected_count
            or str(entry.get("sha256") or "") != _sha256(path)
        ):
            return None
        artifact = path.read_bytes()
    except OSError:
        return None
    if artifact[:8] != MAGIC or int(struct.unpack_from("<Q", artifact, 8)[0]) != expected_count:
        return None
    if any(
        not math.isfinite(value)
        for sample in struct.iter_unpack("<8f", artifact[16:])
        for value in sample
    ):
        return None
    return artifact


def _prepared(
    request: MeshCurvatureRequest,
    key: str,
    metadata: Mapping[str, Any],
    paths: tuple[Path, ...],
    *,
    hit: bool,
) -> PreparedMeshCurvatureResult | None:
    entries = metadata.get("outputs")
    if not isinstance(entries, list) or len(entries) != len(paths):
        return None
    artifacts = []
    for target, entry, path in zip(request.prepared.targets, entries, paths):
        if not isinstance(entry, Mapping):
            return None
        artifact = _read_artifact(
            path,
            entry,
            int(target.topology.get("points", 0) or 0),
        )
        if artifact is None:
            return None
        artifacts.append(artifact)
    accepted = replace(request.prepared, accepted_artifacts=tuple(artifacts))
    return PreparedMeshCurvatureResult(
        request=request,
        prepared=accepted,
        artifacts=tuple(artifacts),
        cache_key=key,
        cache_hit=hit,
    )


def _cached(request: MeshCurvatureRequest, key: str) -> PreparedMeshCurvatureResult | None:
    metadata_path, paths = _paths(request, key)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema") != CACHE_SCHEMA
        or metadata.get("cache_key") != key
    ):
        return None
    return _prepared(request, key, metadata, paths, hit=True)


def _publish(
    request: MeshCurvatureRequest,
    key: str,
    sources: tuple[Path, ...],
    result: Mapping[str, Any],
) -> PreparedMeshCurvatureResult:
    output_metadata = result.get("outputs")
    if not isinstance(output_metadata, list) or len(output_metadata) != len(sources):
        raise NativeMeshError(
            "The isolated curvature result manifest is invalid.",
            error_code="NATIVE_MESH_CURVATURE_ARTIFACT_INVALID",
        )
    metadata_path, artifacts = _paths(request, key)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    temporary_paths = []
    entries = []
    try:
        for index, (target, source, artifact, worker_entry) in enumerate(
            zip(
                request.prepared.targets,
                sources,
                artifacts,
                output_metadata,
            )
        ):
            if not isinstance(worker_entry, Mapping):
                raise NativeMeshError(
                    "The isolated curvature result manifest is invalid.",
                    error_code="NATIVE_MESH_CURVATURE_ARTIFACT_INVALID",
                )
            expected_count = int(target.topology.get("points", 0) or 0)
            expected_size = 16 + expected_count * 32
            digest = _sha256(source)
            if (
                source.stat().st_size != expected_size
                or int(worker_entry.get("bytes", -1)) != expected_size
                or int(worker_entry.get("samples", -1)) != expected_count
                or str(worker_entry.get("sha256") or "") != digest
            ):
                raise NativeMeshError(
                    "An isolated curvature artifact failed authentication.",
                    error_code="NATIVE_MESH_CURVATURE_ARTIFACT_INVALID",
                )
            temporary = atomic_cache_temporary_path(
                artifact.parent, role=f"curvature-{index}", token=token
            )
            temporary_paths.append(temporary)
            with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            os.replace(temporary, artifact)
            entries.append(
                {"sha256": digest, "bytes": expected_size, "samples": expected_count}
            )
        metadata = {
            "schema": CACHE_SCHEMA,
            "cache_key": key,
            "outputs": entries,
        }
        temporary_metadata = atomic_cache_temporary_path(
            metadata_path.parent, role="metadata", token=token
        )
        temporary_paths.append(temporary_metadata)
        with temporary_metadata.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(metadata, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_metadata, metadata_path)
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
    prepared = _prepared(request, key, metadata, artifacts, hit=False)
    if prepared is None:
        raise NativeMeshError(
            "The isolated curvature artifacts failed validation.",
            error_code="NATIVE_MESH_CURVATURE_ARTIFACT_INVALID",
        )
    return prepared


def run_mesh_curvature(
    request: MeshCurvatureRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedMeshCurvatureResult:
    if not isinstance(request, MeshCurvatureRequest):
        raise TypeError("request must be a MeshCurvatureRequest")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(1, "Capturing exact Mesh snapshots")
    from VibeCADNativeMeshTargets import (
        rebind_prepared_mesh_targets,
        snapshot_mesh_targets,
    )

    exact_targets, snapshots = snapshot_mesh_targets(request.prepared.targets)
    request = replace(
        request,
        prepared=rebind_prepared_mesh_targets(request.prepared, exact_targets),
        detached_meshes=snapshots,
    )
    key = cache_key(request)
    cached = _cached(request, key)
    if cached is not None:
        progress(85, "Reusing verified Mesh curvature")
        return cached
    if cancelled():
        raise NativeBackgroundCancelled()
    with tempfile.TemporaryDirectory(prefix="vibecad-mesh-curvature-") as directory:
        root = Path(directory)
        request_path = root / "request.json"
        result_path = root / "result.json"
        entries = []
        output_paths = []
        progress(4, "Writing detached Mesh snapshots")
        for index, mesh in enumerate(request.detached_meshes):
            source_path = root / f"source-{index:03d}.bms"
            output_path = root / f"result-{index:03d}.vcurv"
            mesh.write(str(source_path))
            output_paths.append(output_path)
            entries.append(
                {"source_path": str(source_path), "output_path": str(output_path)}
            )
        if cancelled():
            raise NativeBackgroundCancelled()
        request_path.write_text(
            json.dumps(
                {
                    "schema": REQUEST_SCHEMA,
                    "workspace": str(root),
                    "result_path": str(result_path),
                    "targets": entries,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        progress(10, "Calculating curvature in isolated worker")
        result = run_isolated_mesh_worker(
            freecadcmd=request.freecadcmd,
            child_script=request.child_script,
            request_path=request_path,
            result_path=result_path,
            expected_schema=RESULT_SCHEMA,
            cancelled=cancelled,
            timeout_seconds=TIMEOUT_SECONDS,
            failure_code="NATIVE_MESH_CURVATURE_FAILED",
        )
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(85, "Authenticating Mesh curvature")
        return _publish(request, key, tuple(output_paths), result)


def make_request(prepared: Any) -> MeshCurvatureRequest:
    return MeshCurvatureRequest(
        prepared=prepared,
        detached_meshes=tuple(target.source_mesh for target in prepared.targets),
        cache_root=str(cache_root()),
        freecadcmd=str(freecadcmd_path()),
        child_script=str(Path(__file__).resolve().with_name("VibeCADMeshCurvatureChild.py")),
    )
