# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cached, cancellable, process-isolated retained Mesh modification preparation."""

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
from VibeCADNativeMeshState import mesh_geometry_sha256


CACHE_SCHEMA = "vibecad-mesh-modification-cache-v1"
REQUEST_SCHEMA = "vibecad-mesh-modification-job-v1"
RESULT_SCHEMA = "vibecad-mesh-modification-result-v1"
TIMEOUT_SECONDS = 86_400
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MeshModificationRequest:
    prepared: Any
    detached_meshes: tuple[Any, ...]
    cache_root: str
    freecadcmd: str
    child_script: str


@dataclass(frozen=True, slots=True)
class PreparedMeshModificationResult:
    request: MeshModificationRequest
    output_meshes: tuple[Any, ...]
    output_geometry_sha256: tuple[str, ...]
    changed: tuple[bool, ...]
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


def modification_cache_key(request: MeshModificationRequest) -> str:
    prepared = request.prepared
    payload = {
        "schema": CACHE_SCHEMA,
        "operation": prepared.operation,
        "sources": [target.source_geometry_sha256 for target in prepared.targets],
        "selections": [
            {
                "point_indices": list(target.point_indices),
                "facet_indices": list(target.facet_indices),
            }
            for target in prepared.targets
        ],
        "settings": {
            name: value
            for name, value in prepared.settings.items()
            if not str(name).startswith("_")
        },
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _paths(
    request: MeshModificationRequest,
    key: str,
) -> tuple[Path, tuple[Path, ...], tuple[Path, ...]]:
    directory = Path(request.cache_root) / key[:2] / key
    artifacts = tuple(
        directory / f"result-{index:03d}.bms"
        for index in range(len(request.prepared.targets))
    )
    segment_artifacts = tuple(
        directory / f"result-{index:03d}-segments.json"
        for index in range(len(request.prepared.targets))
    )
    return directory / "metadata.json", artifacts, segment_artifacts


def _restore_segments(mesh: Any, path: Path, expected_count: int) -> bool:
    try:
        if not 1 <= path.stat().st_size <= MAX_ARTIFACT_BYTES:
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, list) or len(raw) != expected_count:
        return False
    for segment in raw:
        if (
            not isinstance(segment, list)
            or any(
                type(value) is not int or not 0 <= value < int(mesh.CountFacets)
                for value in segment
            )
        ):
            return False
    imported = [
        [int(value) for value in mesh.getSegment(index)]
        for index in range(int(mesh.countSegments()))
    ]
    if imported:
        return imported == raw
    for segment in raw:
        mesh.addSegment(segment)
    return int(mesh.countSegments()) == len(raw)


def _load_meshes(
    request: MeshModificationRequest,
    key: str,
    metadata: Mapping[str, Any],
    artifacts: tuple[Path, ...],
    segment_artifacts: tuple[Path, ...],
    *,
    hit: bool,
) -> PreparedMeshModificationResult | None:
    entries = metadata.get("outputs")
    if not isinstance(entries, list) or len(entries) != len(artifacts):
        return None
    import Mesh

    meshes = []
    digests = []
    changed = []
    try:
        for target, entry, artifact, segments_path in zip(
            request.prepared.targets,
            entries,
            artifacts,
            segment_artifacts,
        ):
            if not isinstance(entry, Mapping):
                return None
            size = artifact.stat().st_size
            if not 1 <= size <= MAX_ARTIFACT_BYTES or entry.get("artifact_sha256") != _sha256(
                artifact
            ):
                return None
            mesh = Mesh.Mesh(str(artifact))
            if (
                not 1 <= segments_path.stat().st_size <= MAX_ARTIFACT_BYTES
                or entry.get("segments_sha256") != _sha256(segments_path)
            ):
                return None
            if not _restore_segments(
                mesh,
                segments_path,
                int(entry.get("segments", -1)),
            ):
                return None
            geometry_sha = mesh_geometry_sha256(mesh)
            if geometry_sha != entry.get("geometry_sha256"):
                return None
            meshes.append(mesh)
            digests.append(geometry_sha)
            changed.append(geometry_sha != target.source_geometry_sha256)
    except (OSError, RuntimeError, ValueError):
        return None
    return PreparedMeshModificationResult(
        request=request,
        output_meshes=tuple(meshes),
        output_geometry_sha256=tuple(digests),
        changed=tuple(changed),
        cache_key=key,
        cache_hit=hit,
    )


def _cached(
    request: MeshModificationRequest,
    key: str,
) -> PreparedMeshModificationResult | None:
    metadata_path, artifacts, segment_artifacts = _paths(request, key)
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
    return _load_meshes(
        request,
        key,
        metadata,
        artifacts,
        segment_artifacts,
        hit=True,
    )


def _publish(
    request: MeshModificationRequest,
    key: str,
    sources: tuple[Path, ...],
    segment_sources: tuple[Path, ...],
) -> PreparedMeshModificationResult:
    metadata_path, artifacts, segment_artifacts = _paths(request, key)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    entries = []
    temporary_artifacts = []
    try:
        for source, segment_source, artifact, segment_artifact in zip(
            sources,
            segment_sources,
            artifacts,
            segment_artifacts,
        ):
            size = source.stat().st_size
            if not 1 <= size <= MAX_ARTIFACT_BYTES:
                raise NativeMeshError(
                    "An isolated Mesh modification artifact is empty or exceeds 16 GiB.",
                    error_code="NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
                )
            if not 1 <= segment_source.stat().st_size <= MAX_ARTIFACT_BYTES:
                raise NativeMeshError(
                    "An isolated Mesh segment artifact is empty or exceeds 16 GiB.",
                    error_code="NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
                )
            temporary = artifact.with_name(
                f".{artifact.name}.{os.getpid()}.{token}.tmp"
            )
            temporary_artifacts.append(temporary)
            with source.open("rb") as source_stream, temporary.open("wb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            os.replace(temporary, artifact)
            segment_temporary = segment_artifact.with_name(
                f".{segment_artifact.name}.{os.getpid()}.{token}.tmp"
            )
            temporary_artifacts.append(segment_temporary)
            with segment_source.open("rb") as source_stream, segment_temporary.open(
                "wb"
            ) as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            os.replace(segment_temporary, segment_artifact)
            raw_segments = json.loads(segment_artifact.read_text(encoding="utf-8"))
            if not isinstance(raw_segments, list):
                raise ValueError("invalid Mesh segment artifact")
            entries.append(
                {
                    "artifact_sha256": _sha256(artifact),
                    "segments_sha256": _sha256(segment_artifact),
                    "segments": len(raw_segments),
                }
            )

        import Mesh

        try:
            for entry, artifact, segments_path in zip(
                entries,
                artifacts,
                segment_artifacts,
            ):
                mesh = Mesh.Mesh(str(artifact))
                if not _restore_segments(mesh, segments_path, int(entry["segments"])):
                    raise ValueError("invalid Mesh segment artifact")
                entry["geometry_sha256"] = mesh_geometry_sha256(mesh)
        except Exception as exc:
            raise NativeMeshError(
                "The isolated Mesh modification artifacts failed authentication.",
                error_code="NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
            ) from exc
        metadata = {"schema": CACHE_SCHEMA, "cache_key": key, "outputs": entries}
        metadata_temp = metadata_path.with_name(
            f".{metadata_path.name}.{os.getpid()}.{token}.tmp"
        )
        try:
            with metadata_temp.open("w", encoding="utf-8") as stream:
                stream.write(json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(metadata_temp, metadata_path)
        finally:
            metadata_temp.unlink(missing_ok=True)
        loaded = _load_meshes(
            request,
            key,
            metadata,
            artifacts,
            segment_artifacts,
            hit=False,
        )
        if loaded is None:
            raise NativeMeshError(
                "The isolated Mesh modification artifacts failed authentication.",
                error_code="NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
            )
        return loaded
    finally:
        for temporary in temporary_artifacts:
            temporary.unlink(missing_ok=True)


def run_mesh_modification(
    request: MeshModificationRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedMeshModificationResult:
    if not isinstance(request, MeshModificationRequest):
        raise TypeError("request must be a MeshModificationRequest")
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
    key = modification_cache_key(request)
    cached = _cached(request, key)
    if cached is not None:
        progress(85, "Reusing verified Mesh modification")
        return cached
    if cancelled():
        raise NativeBackgroundCancelled()
    with tempfile.TemporaryDirectory(prefix="vibecad-mesh-modification-") as directory:
        root = Path(directory)
        request_path = root / "request.json"
        result_path = root / "result.json"
        target_payload = []
        output_paths = []
        output_segments_paths = []
        progress(4, "Writing detached Mesh snapshots")
        for index, (target, mesh) in enumerate(
            zip(request.prepared.targets, request.detached_meshes)
        ):
            source_path = root / f"source-{index:03d}.bms"
            source_segments_path = root / f"source-{index:03d}-segments.json"
            output_path = root / f"result-{index:03d}.bms"
            output_segments_path = root / f"result-{index:03d}-segments.json"
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
            output_paths.append(output_path)
            output_segments_paths.append(output_segments_path)
            target_payload.append(
                {
                    "source_path": str(source_path),
                    "source_segments_path": str(source_segments_path),
                    "output_path": str(output_path),
                    "output_segments_path": str(output_segments_path),
                    "point_indices": list(target.point_indices),
                    "facet_indices": list(target.facet_indices),
                }
            )
        if cancelled():
            raise NativeBackgroundCancelled()
        request_path.write_text(
            json.dumps(
                {
                    "schema": REQUEST_SCHEMA,
                    "workspace": str(root),
                    "result_path": str(result_path),
                    "operation": request.prepared.operation,
                    "settings": {
                        name: value
                        for name, value in request.prepared.settings.items()
                        if not str(name).startswith("_")
                    },
                    "targets": target_payload,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        progress(10, "Computing Mesh modification in isolated worker")
        run_isolated_mesh_worker(
            freecadcmd=request.freecadcmd,
            child_script=request.child_script,
            request_path=request_path,
            result_path=result_path,
            expected_schema=RESULT_SCHEMA,
            cancelled=cancelled,
            timeout_seconds=TIMEOUT_SECONDS,
            failure_code="NATIVE_MESH_MODIFICATION_FAILED",
        )
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(85, "Authenticating Mesh modification")
        return _publish(
            request,
            key,
            tuple(output_paths),
            tuple(output_segments_paths),
        )


def make_request(prepared: Any) -> MeshModificationRequest:
    return MeshModificationRequest(
        prepared=prepared,
        detached_meshes=tuple(target.source_mesh for target in prepared.targets),
        cache_root=str(cache_root()),
        freecadcmd=str(freecadcmd_path()),
        child_script=str(Path(__file__).resolve().with_name("VibeCADMeshModificationChild.py")),
    )
