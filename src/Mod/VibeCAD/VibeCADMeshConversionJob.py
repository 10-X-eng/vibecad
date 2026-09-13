# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cached, cancellable, process-isolated Mesh-to-BREP preparation."""

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

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADIsolatedMeshWorker import freecadcmd_path, run_isolated_mesh_worker
from VibeCADMeshCacheAtomic import atomic_cache_temporary_path
from VibeCADNativeMeshErrors import NativeMeshError


CACHE_SCHEMA = "vibecad-mesh-conversion-cache-v1"
JOB_SCHEMA = "vibecad-mesh-conversion-job-v1"
RESULT_SCHEMA = "vibecad-mesh-conversion-result-v1"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024
CONVERSION_TIMEOUT_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class MeshConversionRequest:
    target: Any
    detached_mesh: Any
    source_placement: Mapping[str, Any]
    label: str
    tolerance_mm: float
    sew_adjacent_faces: bool
    make_solid: bool
    cache_root: str
    freecadcmd: str
    child_script: str
    source_topology: str = "closed"


@dataclass(frozen=True, slots=True)
class PreparedMeshConversion:
    request: MeshConversionRequest
    artifact_path: str
    artifact_sha256: str
    cache_key: str
    cache_hit: bool
    shape_type: str
    representation: str
    topology: Mapping[str, int]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_root() -> Path:
    import FreeCAD as App

    return Path(str(App.getUserAppDataDir())) / "VibeCAD" / "cache" / CACHE_SCHEMA


def conversion_cache_key(request: MeshConversionRequest) -> str:
    payload = {
        "schema": CACHE_SCHEMA,
        "mesh_geometry_sha256": str(request.target.source_geometry_sha256),
        "placement": dict(request.source_placement),
        "tolerance_mm": request.tolerance_mm,
        "sew_adjacent_faces": request.sew_adjacent_faces,
        "make_solid": request.make_solid,
        "source_topology": request.source_topology,
        "refine_shape": True,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_paths(request: MeshConversionRequest, key: str) -> tuple[Path, Path]:
    root = Path(request.cache_root)
    directory = root / key[:2] / key
    return directory / "shape.brep", directory / "metadata.json"


def _cached(request: MeshConversionRequest, key: str) -> PreparedMeshConversion | None:
    artifact, metadata_path = _cache_paths(request, key)
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
        or not isinstance(metadata.get("topology"), dict)
    ):
        return None
    return PreparedMeshConversion(
        request=request,
        artifact_path=str(artifact),
        artifact_sha256=str(metadata["artifact_sha256"]),
        cache_key=key,
        cache_hit=True,
        shape_type=str(metadata.get("shape_type") or ""),
        representation=str(metadata.get("representation") or "faceted_shape"),
        topology={str(name): int(value) for name, value in metadata["topology"].items()},
    )


def _publish_cache(
    request: MeshConversionRequest,
    key: str,
    source_artifact: Path,
    result: Mapping[str, Any],
) -> PreparedMeshConversion:
    size = source_artifact.stat().st_size
    if not 1 <= size <= MAX_ARTIFACT_BYTES:
        raise NativeMeshError(
            "The isolated Mesh conversion BREP is empty or exceeds 16 GiB.",
            error_code="NATIVE_MESH_CONVERSION_ARTIFACT_INVALID",
        )
    digest = _sha256(source_artifact)
    artifact, metadata_path = _cache_paths(request, key)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    artifact_temp = atomic_cache_temporary_path(
        artifact.parent, role="brep-artifact", token=token
    )
    metadata_temp = atomic_cache_temporary_path(
        metadata_path.parent, role="metadata", token=token
    )
    metadata = {
        "schema": CACHE_SCHEMA,
        "cache_key": key,
        "artifact_sha256": digest,
        "shape_type": str(result.get("shape_type") or ""),
        "representation": str(result.get("representation") or "faceted_shape"),
        "topology": dict(result.get("topology") or {}),
    }
    try:
        with source_artifact.open("rb") as source_stream, artifact_temp.open("wb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        os.replace(artifact_temp, artifact)
        with metadata_temp.open("w", encoding="utf-8") as metadata_stream:
            metadata_stream.write(
                json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n"
            )
            metadata_stream.flush()
            os.fsync(metadata_stream.fileno())
        os.replace(metadata_temp, metadata_path)
    finally:
        artifact_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)
    return PreparedMeshConversion(
        request=request,
        artifact_path=str(artifact),
        artifact_sha256=digest,
        cache_key=key,
        cache_hit=False,
        shape_type=metadata["shape_type"],
        representation=metadata["representation"],
        topology={str(name): int(value) for name, value in metadata["topology"].items()},
    )


def run_mesh_conversion(
    request: MeshConversionRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedMeshConversion:
    if not isinstance(request, MeshConversionRequest):
        raise TypeError("request must be a MeshConversionRequest")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(1, "Capturing exact Mesh snapshot")
    from VibeCADNativeMeshTargets import snapshot_mesh_targets

    exact_targets, snapshots = snapshot_mesh_targets((request.target,))
    detached = snapshots[0]
    placement = detached.Placement
    detached.Placement = type(placement)()
    detached.transform(placement.toMatrix())
    request = replace(
        request,
        target=exact_targets[0],
        detached_mesh=detached,
    )
    key = conversion_cache_key(request)
    cached = _cached(request, key)
    if cached is not None:
        progress(85, "Reusing verified Mesh conversion")
        return cached
    if cancelled():
        raise NativeBackgroundCancelled()
    with tempfile.TemporaryDirectory(prefix="vibecad-mesh-conversion-") as directory:
        root = Path(directory)
        source_path = root / "source.bms"
        output_path = root / "shape.brep"
        request_path = root / "request.json"
        result_path = root / "result.json"
        progress(4, "Writing detached Mesh snapshot")
        try:
            request.detached_mesh.write(str(source_path))
        except Exception as exc:
            raise NativeMeshError(
                "The detached Mesh snapshot could not be written.",
                error_code="NATIVE_MESH_CONVERSION_ARTIFACT_INVALID",
            ) from exc
        if cancelled():
            raise NativeBackgroundCancelled()
        request_path.write_text(
            json.dumps(
                {
                    "schema": JOB_SCHEMA,
                    "workspace": str(root),
                    "source_path": str(source_path),
                    "output_path": str(output_path),
                    "result_path": str(result_path),
                    "tolerance_mm": request.tolerance_mm,
                    "sew_adjacent_faces": request.sew_adjacent_faces,
                    "make_solid": request.make_solid,
                    "source_topology": request.source_topology,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        progress(10, "Building BREP in isolated worker")
        result = run_isolated_mesh_worker(
            freecadcmd=request.freecadcmd,
            child_script=request.child_script,
            request_path=request_path,
            result_path=result_path,
            expected_schema=RESULT_SCHEMA,
            cancelled=cancelled,
            timeout_seconds=CONVERSION_TIMEOUT_SECONDS,
            failure_code="NATIVE_MESH_CONVERSION_FAILED",
        )
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(85, "Authenticating converted BREP")
        return _publish_cache(request, key, output_path, result)


def make_request(
    *,
    target: Any,
    detached_mesh: Any,
    source_placement: Mapping[str, Any],
    label: str,
    tolerance_mm: float,
    sew_adjacent_faces: bool,
    make_solid: bool,
    source_topology: str = "closed",
) -> MeshConversionRequest:
    if source_topology not in {"closed", "sewable"}:
        raise ValueError("source_topology must be closed or sewable")
    return MeshConversionRequest(
        target=target,
        detached_mesh=detached_mesh,
        source_placement=dict(source_placement),
        label=label,
        tolerance_mm=tolerance_mm,
        sew_adjacent_faces=sew_adjacent_faces,
        make_solid=make_solid,
        cache_root=str(cache_root()),
        freecadcmd=str(freecadcmd_path()),
        child_script=str(Path(__file__).resolve().with_name("VibeCADMeshConversionChild.py")),
        source_topology=source_topology,
    )
