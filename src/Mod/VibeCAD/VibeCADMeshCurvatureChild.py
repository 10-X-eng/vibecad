# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated worker for exact per-vertex Mesh curvature artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
from typing import Any, Mapping


REQUEST_SCHEMA = "vibecad-mesh-curvature-job-v1"
RESULT_SCHEMA = "vibecad-mesh-curvature-result-v1"
MAGIC = b"VCURV01\0"


class CurvatureFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


def _path(value: Any, root: Path, field: str) -> Path:
    path = Path(str(value or "")).resolve()
    if path.parent != root:
        raise CurvatureFailure(
            "NATIVE_MESH_CURVATURE_ARTIFACT_INVALID",
            f"{field} must be one file in the private curvature workspace.",
        )
    return path


def _artifact_count(data: bytes) -> int:
    if len(data) < 16 or data[:8] != MAGIC:
        raise CurvatureFailure(
            "NATIVE_MESH_CURVATURE_ARTIFACT_INVALID",
            "The curvature worker produced an invalid artifact header.",
        )
    count = int(struct.unpack_from("<Q", data, 8)[0])
    if len(data) != 16 + count * 32:
        raise CurvatureFailure(
            "NATIVE_MESH_CURVATURE_ARTIFACT_INVALID",
            "The curvature worker produced an invalid artifact size.",
        )
    return count


def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema") != REQUEST_SCHEMA:
        raise CurvatureFailure(
            "NATIVE_MESH_CURVATURE_REQUEST_INVALID",
            "The isolated curvature request has an unsupported schema.",
        )
    root = Path(str(request.get("workspace") or "")).resolve()
    entries = request.get("targets")
    if not root.is_dir() or not isinstance(entries, list) or not entries:
        raise CurvatureFailure(
            "NATIVE_MESH_CURVATURE_REQUEST_INVALID",
            "The isolated curvature request is incomplete.",
        )

    import Mesh

    outputs = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise CurvatureFailure(
                "NATIVE_MESH_CURVATURE_REQUEST_INVALID",
                "Every curvature target must be an object.",
            )
        source_path = _path(entry.get("source_path"), root, "source_path")
        output_path = _path(entry.get("output_path"), root, "output_path")
        mesh = Mesh.Mesh(str(source_path))
        if int(mesh.CountFacets) < 1 or int(mesh.CountPoints) < 3:
            raise CurvatureFailure(
                "NATIVE_MESH_CURVATURE_SOURCE_EMPTY",
                "A detached curvature source contains no usable facets.",
            )
        artifact = bytes(Mesh.curvatureArtifact(mesh))
        count = _artifact_count(artifact)
        if count != int(mesh.CountPoints):
            raise CurvatureFailure(
                "NATIVE_MESH_CURVATURE_ARTIFACT_INVALID",
                "The curvature sample count does not match the source Mesh.",
            )
        output_path.write_bytes(artifact)
        outputs.append(
            {
                "sha256": hashlib.sha256(artifact).hexdigest(),
                "bytes": len(artifact),
                "samples": count,
            }
        )
    return {"schema": RESULT_SCHEMA, "ok": True, "outputs": outputs}


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The curvature request is not an object.")
    root = Path(str(request.get("workspace") or "")).resolve()
    result_path = _path(request.get("result_path"), root, "result_path")
    try:
        result = execute(request)
    except CurvatureFailure as exc:
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
            "failure_code": "NATIVE_MESH_CURVATURE_FAILED",
            "error": "The isolated Mesh curvature calculation failed.",
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


if __name__ != "VibeCADMeshCurvatureChild":
    raise SystemExit(main())
