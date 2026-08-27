# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cached, cancellable preparation for retained Mesh cuts and sections."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
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


CACHE_SCHEMA = "vibecad-mesh-cut-cache-v1"
REQUEST_SCHEMA = "vibecad-mesh-cut-job-v1"
RESULT_SCHEMA = "vibecad-mesh-cut-result-v1"
TIMEOUT_SECONDS = 86_400
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MeshCutRequest:
    prepared: Any
    detached_meshes: tuple[Any, ...]
    parameters: Mapping[str, Any]
    output_kinds: tuple[str, ...]
    cache_root: str
    freecadcmd: str
    child_script: str


@dataclass(frozen=True, slots=True)
class PreparedMeshCutResult:
    request: MeshCutRequest
    prepared: Any
    artifact_sha256: tuple[str, ...]
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


def _cache_key(request: MeshCutRequest) -> str:
    targets = getattr(request.prepared, "targets", None)
    if targets is None:
        target = getattr(request.prepared, "target", None)
        targets = (target,) if target is not None else ()
    payload = {
        "schema": CACHE_SCHEMA,
        "operation": str(request.prepared.operation),
        "sources": [str(target.source_geometry_sha256) for target in targets],
        "parameters": dict(request.parameters),
        "outputs": list(request.output_kinds),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _paths(request: MeshCutRequest, key: str) -> tuple[Path, tuple[Path, ...]]:
    directory = Path(request.cache_root) / key[:2] / key
    artifacts = tuple(
        directory / f"result-{index:03d}.{kind}"
        for index, kind in enumerate(request.output_kinds)
    )
    return directory / "metadata.json", artifacts


def _shape_topology(shape: Any) -> dict[str, int]:
    return {
        "vertices": len(shape.Vertexes),
        "edges": len(shape.Edges),
        "wires": len(shape.Wires),
        "faces": len(shape.Faces),
        "shells": len(shape.Shells),
        "solids": len(shape.Solids),
    }


def _placement(mesh: Any) -> dict[str, list[float]]:
    placement = mesh.Placement
    return {
        "base": [
            float(placement.Base.x),
            float(placement.Base.y),
            float(placement.Base.z),
        ],
        "quaternion": [float(value) for value in placement.Rotation.Q],
    }


def _restore_placement(mesh: Any, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("Mesh artifact placement is missing")
    base = value.get("base")
    quaternion = value.get("quaternion")
    if not isinstance(base, list) or len(base) != 3 or not isinstance(quaternion, list) or len(
        quaternion
    ) != 4:
        raise ValueError("Mesh artifact placement is invalid")
    import FreeCAD as App

    base_values = tuple(float(component) for component in base)
    quaternion_values = tuple(float(component) for component in quaternion)
    if not all(math.isfinite(component) for component in (*base_values, *quaternion_values)):
        raise ValueError("Mesh artifact placement is invalid")
    mesh.Placement = App.Placement(
        App.Vector(*base_values),
        App.Rotation(*quaternion_values),
    )


def _load(
    request: MeshCutRequest,
    key: str,
    metadata: Mapping[str, Any],
    artifacts: tuple[Path, ...],
    *,
    hit: bool,
) -> PreparedMeshCutResult | None:
    entries = metadata.get("outputs")
    if not isinstance(entries, list) or len(entries) != len(artifacts):
        return None
    meshes = []
    shapes = []
    digests = []
    try:
        for kind, path, entry in zip(request.output_kinds, artifacts, entries, strict=True):
            if not isinstance(entry, Mapping):
                return None
            size = path.stat().st_size
            digest = _sha256(path)
            if (
                not 1 <= size <= MAX_ARTIFACT_BYTES
                or str(entry.get("kind") or "") != kind
                or int(entry.get("bytes", -1)) != size
                or str(entry.get("artifact_sha256") or "") != digest
            ):
                return None
            if kind == "bms":
                import Mesh

                mesh = Mesh.Mesh(str(path))
                _restore_placement(mesh, entry.get("placement"))
                geometry = mesh_geometry_sha256(mesh)
                if (
                    int(mesh.CountFacets) < 1
                    or str(entry.get("geometry_sha256") or "") != geometry
                    or int(entry.get("points", -1)) != int(mesh.CountPoints)
                    or int(entry.get("facets", -1)) != int(mesh.CountFacets)
                ):
                    return None
                meshes.append(mesh)
            elif kind == "brep":
                import Part

                shape = Part.Shape()
                shape.importBrep(str(path))
                topology = _shape_topology(shape)
                if (
                    shape.isNull()
                    or not shape.isValid()
                    or topology["edges"] < 1
                    or dict(entry.get("topology") or {}) != topology
                ):
                    return None
                shapes.append(shape)
            else:
                return None
            digests.append(digest)
    except (OSError, RuntimeError, ValueError):
        return None

    from VibeCADNativeMeshPlane import (
        PreparedMeshCrossSections,
        PreparedMeshPlaneSection,
        PreparedMeshPlaneTrim,
    )
    from VibeCADNativeMeshPolygon import PreparedMeshPolygon
    from VibeCADNativeMeshViewportPolygon import PreparedMeshViewportPolygon

    prepared = request.prepared
    if isinstance(prepared, PreparedMeshPolygon):
        geometry = tuple(mesh_geometry_sha256(mesh) for mesh in meshes)
        if (
            len(meshes) != len(prepared.regions)
            or any(value == prepared.target.source_geometry_sha256 for value in geometry)
            or len(set(geometry)) != len(geometry)
        ):
            return None
        prepared = replace(
            prepared,
            expected_result_sha256=geometry,
            accepted_meshes=tuple(meshes),
        )
    elif isinstance(prepared, PreparedMeshViewportPolygon):
        geometry = tuple(mesh_geometry_sha256(mesh) for mesh in meshes)
        if len(meshes) != len(prepared.result_targets) or any(
            geometry[index]
            == prepared.targets[target_index].source_geometry_sha256
            for index, target_index in enumerate(prepared.result_targets)
        ):
            return None
        for target_index in range(len(prepared.targets)):
            target_geometry = {
                geometry[index]
                for index, result_target in enumerate(prepared.result_targets)
                if result_target == target_index
            }
            if len(target_geometry) != sum(
                1 for result_target in prepared.result_targets if result_target == target_index
            ):
                return None
        prepared = replace(
            prepared,
            expected_result_sha256=geometry,
            accepted_meshes=tuple(meshes),
        )
    elif isinstance(prepared, PreparedMeshPlaneTrim):
        geometry = tuple(mesh_geometry_sha256(mesh) for mesh in meshes)
        if (
            len(meshes) != len(prepared.sides)
            or any(value == prepared.target.source_geometry_sha256 for value in geometry)
            or len(set(geometry)) != len(geometry)
        ):
            return None
        prepared = replace(
            prepared,
            expected_result_sha256=geometry,
            accepted_meshes=tuple(meshes),
        )
    elif isinstance(prepared, PreparedMeshPlaneSection):
        if len(shapes) != 1:
            return None
        prepared = replace(prepared, accepted_shapes=tuple(shapes))
    elif isinstance(prepared, PreparedMeshCrossSections):
        if len(shapes) != len(prepared.targets):
            return None
        prepared = replace(prepared, accepted_shapes=tuple(shapes))
    else:
        return None
    return PreparedMeshCutResult(request, prepared, tuple(digests), key, hit)


def _cached(request: MeshCutRequest, key: str) -> PreparedMeshCutResult | None:
    metadata_path, artifacts = _paths(request, key)
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
    return _load(request, key, metadata, artifacts, hit=True)


def _publish(
    request: MeshCutRequest,
    key: str,
    sources: tuple[Path, ...],
    worker_result: Mapping[str, Any],
) -> PreparedMeshCutResult:
    outputs = worker_result.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != len(sources):
        raise NativeMeshError(
            "The isolated Mesh cut manifest is invalid.",
            error_code="NATIVE_MESH_CUT_ARTIFACT_INVALID",
        )
    metadata_path, artifacts = _paths(request, key)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    entries = []
    temporary_paths = []
    try:
        for kind, source, artifact, output in zip(
            request.output_kinds, sources, artifacts, outputs, strict=True
        ):
            if not isinstance(output, Mapping):
                raise NativeMeshError(
                    "The isolated Mesh cut manifest is invalid.",
                    error_code="NATIVE_MESH_CUT_ARTIFACT_INVALID",
                )
            size = source.stat().st_size
            digest = _sha256(source)
            if (
                not 1 <= size <= MAX_ARTIFACT_BYTES
                or str(output.get("kind") or "") != kind
                or int(output.get("bytes", -1)) != size
                or str(output.get("artifact_sha256") or "") != digest
            ):
                raise NativeMeshError(
                    "An isolated Mesh cut artifact failed authentication.",
                    error_code="NATIVE_MESH_CUT_ARTIFACT_INVALID",
                )
            temporary = artifact.with_name(
                f".{artifact.name}.{os.getpid()}.{token}.tmp"
            )
            temporary_paths.append(temporary)
            with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            os.replace(temporary, artifact)
            entries.append(dict(output))
        metadata = {"schema": CACHE_SCHEMA, "cache_key": key, "outputs": entries}
        temporary_metadata = metadata_path.with_name(
            f".{metadata_path.name}.{os.getpid()}.{token}.tmp"
        )
        temporary_paths.append(temporary_metadata)
        with temporary_metadata.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_metadata, metadata_path)
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
    loaded = _load(request, key, metadata, artifacts, hit=False)
    if loaded is None:
        raise NativeMeshError(
            "The isolated Mesh cut artifacts failed validation.",
            error_code="NATIVE_MESH_CUT_ARTIFACT_INVALID",
        )
    return loaded


def run_mesh_cut(
    request: MeshCutRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedMeshCutResult:
    if not isinstance(request, MeshCutRequest):
        raise TypeError("request must be a MeshCutRequest")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(1, "Capturing exact Mesh snapshots")
    from VibeCADNativeMeshTargets import (
        rebind_prepared_mesh_targets,
        snapshot_mesh_targets,
    )

    prepared_targets = getattr(request.prepared, "targets", None)
    if prepared_targets is None:
        prepared_target = getattr(request.prepared, "target", None)
        prepared_targets = (prepared_target,) if prepared_target is not None else ()
    exact_targets, snapshots = snapshot_mesh_targets(tuple(prepared_targets))
    request = replace(
        request,
        prepared=rebind_prepared_mesh_targets(request.prepared, exact_targets),
        detached_meshes=snapshots,
    )
    key = _cache_key(request)
    cached = _cached(request, key)
    if cached is not None:
        progress(85, "Reusing verified Mesh cut")
        return cached
    if cancelled():
        raise NativeBackgroundCancelled()
    with tempfile.TemporaryDirectory(prefix="vibecad-mesh-cut-") as directory:
        root = Path(directory)
        request_path = root / "request.json"
        result_path = root / "result.json"
        source_payload = []
        output_paths = []
        progress(4, "Writing detached Mesh snapshots")
        for index, mesh in enumerate(request.detached_meshes):
            path = root / f"source-{index:03d}.bms"
            mesh.write(str(path))
            source_payload.append({"path": str(path), "placement": _placement(mesh)})
        for index, kind in enumerate(request.output_kinds):
            output_paths.append(root / f"result-{index:03d}.{kind}")
        if cancelled():
            raise NativeBackgroundCancelled()
        request_path.write_text(
            json.dumps(
                {
                    "schema": REQUEST_SCHEMA,
                    "workspace": str(root),
                    "result_path": str(result_path),
                    "operation": str(request.prepared.operation),
                    "parameters": dict(request.parameters),
                    "sources": source_payload,
                    "outputs": [
                        {"kind": kind, "path": str(path)}
                        for kind, path in zip(request.output_kinds, output_paths, strict=True)
                    ],
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        progress(10, "Computing Mesh cut in isolated worker")
        result = run_isolated_mesh_worker(
            freecadcmd=request.freecadcmd,
            child_script=request.child_script,
            request_path=request_path,
            result_path=result_path,
            expected_schema=RESULT_SCHEMA,
            cancelled=cancelled,
            timeout_seconds=TIMEOUT_SECONDS,
            failure_code="NATIVE_MESH_CUT_FAILED",
        )
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(85, "Authenticating Mesh cut")
        return _publish(request, key, tuple(output_paths), result)


def _vector(value: Any) -> list[float]:
    return [float(value.x), float(value.y), float(value.z)]


def make_request(prepared: Any) -> MeshCutRequest:
    from VibeCADNativeMeshPlane import (
        PreparedMeshCrossSections,
        PreparedMeshPlaneSection,
        PreparedMeshPlaneTrim,
        _plane_vectors,
    )
    from VibeCADNativeMeshPolygon import PreparedMeshPolygon
    from VibeCADNativeMeshViewportPolygon import PreparedMeshViewportPolygon

    if isinstance(prepared, PreparedMeshPolygon):
        targets = (prepared.target,)
        parameters = {
            "polygon": [list(point) for point in prepared.polygon],
            "regions": list(prepared.regions),
        }
        kinds = ("bms",) * len(prepared.regions)
    elif isinstance(prepared, PreparedMeshViewportPolygon):
        targets = prepared.targets
        source_regions = [
            [
                region
                for region, result_target in zip(
                    prepared.regions, prepared.result_targets, strict=True
                )
                if result_target == target_index
            ]
            for target_index in range(len(targets))
        ]
        parameters = {
            "polygon": [list(point) for point in prepared.polygon],
            "projection_matrix": list(prepared.projection_matrix),
            "action": "cut" if prepared.operation == "viewport_cut" else "trim",
            "source_regions": source_regions,
        }
        kinds = ("bms",) * len(prepared.result_targets)
    elif isinstance(prepared, PreparedMeshPlaneTrim):
        targets = (prepared.target,)
        base, normal = _plane_vectors(prepared.plane.plane)
        parameters = {
            "plane_base": _vector(base),
            "plane_normal": _vector(normal),
            "sides": list(prepared.sides),
        }
        kinds = ("bms",) * len(prepared.sides)
    elif isinstance(prepared, PreparedMeshPlaneSection):
        targets = (prepared.target,)
        base, normal = _plane_vectors(prepared.plane.plane)
        parameters = {
            "plane_base": _vector(base),
            "plane_normal": _vector(normal),
            "minimum_length_mm": prepared.minimum_length_mm,
            "connect_edges": prepared.connect_edges,
        }
        kinds = ("brep",)
    elif isinstance(prepared, PreparedMeshCrossSections):
        targets = prepared.targets
        parameters = {
            "normal": list(prepared.normal),
            "positions_mm": list(prepared.positions_mm),
            "epsilon_mm": prepared.epsilon_mm,
            "connect_edges": prepared.connect_edges,
        }
        kinds = ("brep",) * len(prepared.targets)
    else:
        raise TypeError("prepared is not a Mesh cut operation")
    return MeshCutRequest(
        prepared=prepared,
        detached_meshes=tuple(target.source_mesh for target in targets),
        parameters=parameters,
        output_kinds=kinds,
        cache_root=str(cache_root()),
        freecadcmd=str(freecadcmd_path()),
        child_script=str(Path(__file__).resolve().with_name("VibeCADMeshCutChild.py")),
    )
