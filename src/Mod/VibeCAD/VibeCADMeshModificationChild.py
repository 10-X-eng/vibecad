# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd worker for retained Mesh modifications."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping

from VibeCADMeshModificationOperation import FEATURE_TYPES, configure_mesh_feature


_SCHEMA = "vibecad-mesh-modification-job-v1"
_RESULT_SCHEMA = "vibecad-mesh-modification-result-v1"


class ModificationFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


def _path(value: Any, root: Path, field: str) -> Path:
    path = Path(str(value or "")).resolve()
    if path.parent != root:
        raise ModificationFailure(
            "NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
            f"{field} must be one file in the private modification workspace.",
        )
    return path


def _restore_segments(mesh: Any, path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModificationFailure(
            "NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
            "A detached Mesh segment artifact is invalid.",
        ) from exc
    facet_count = int(mesh.CountFacets)
    if not isinstance(raw, list):
        raise ModificationFailure(
            "NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
            "A detached Mesh segment artifact is invalid.",
        )
    for segment in raw:
        if (
            not isinstance(segment, list)
            or any(
                type(value) is not int or not 0 <= value < facet_count
                for value in segment
            )
        ):
            raise ModificationFailure(
                "NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
                "A detached Mesh segment contains an invalid facet index.",
            )
        mesh.addSegment(segment)


def _write_segments(mesh: Any, path: Path) -> int:
    segments = [
        [int(value) for value in mesh.getSegment(index)]
        for index in range(int(mesh.countSegments()))
    ]
    path.write_text(
        json.dumps(segments, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return len(segments)


def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema") != _SCHEMA:
        raise ModificationFailure(
            "NATIVE_MESH_MODIFICATION_REQUEST_INVALID",
            "The isolated Mesh modification request has an unsupported schema.",
        )
    root = Path(str(request.get("workspace") or "")).resolve()
    if not root.is_dir():
        raise ModificationFailure(
            "NATIVE_MESH_MODIFICATION_REQUEST_INVALID",
            "The isolated Mesh modification workspace is unavailable.",
        )
    operation = str(request.get("operation") or "")
    type_id = FEATURE_TYPES.get(operation)
    if type_id is None:
        raise ModificationFailure(
            "NATIVE_MESH_MODIFICATION_REQUEST_INVALID",
            "The isolated Mesh modification operation is unsupported.",
        )
    settings = request.get("settings")
    targets = request.get("targets")
    if not isinstance(settings, Mapping) or not isinstance(targets, list) or not targets:
        raise ModificationFailure(
            "NATIVE_MESH_MODIFICATION_REQUEST_INVALID",
            "The isolated Mesh modification request is incomplete.",
        )

    import FreeCAD as App
    import Mesh  # noqa: F401 - registers retained Mesh feature types

    document = App.newDocument("VibeCADMeshModificationWorker")
    outputs = []
    try:
        for index, target in enumerate(targets):
            if not isinstance(target, Mapping):
                raise ModificationFailure(
                    "NATIVE_MESH_MODIFICATION_REQUEST_INVALID",
                    "Every isolated Mesh modification target must be an object.",
                )
            source_path = _path(target.get("source_path"), root, "source_path")
            source_segments_path = _path(
                target.get("source_segments_path"),
                root,
                "source_segments_path",
            )
            output_path = _path(target.get("output_path"), root, "output_path")
            output_segments_path = _path(
                target.get("output_segments_path"),
                root,
                "output_segments_path",
            )
            source_mesh = Mesh.Mesh(str(source_path))
            _restore_segments(source_mesh, source_segments_path)
            if int(source_mesh.CountFacets) < 1:
                raise ModificationFailure(
                    "NATIVE_MESH_MODIFICATION_SOURCE_EMPTY",
                    "A detached Mesh modification source contains no facets.",
                )
            source = document.addObject("Mesh::Feature", f"Source{index}")
            source.Mesh = source_mesh
            result = document.addObject(type_id, f"Result{index}")
            result.Source = source
            configure_mesh_feature(
                result,
                source_mesh=source_mesh,
                operation=operation,
                settings=settings,
                point_indices=target.get("point_indices") or (),
                facet_indices=target.get("facet_indices") or (),
            )
            if document.recompute([result], True, True) is False or not result.isValid():
                detail = str(result.getStatusString() or "").strip()
                raise ModificationFailure(
                    "NATIVE_MESH_MODIFICATION_FAILED",
                    detail or "The detached Mesh modification failed.",
                )
            result.Mesh.write(str(output_path))
            segment_count = _write_segments(result.Mesh, output_segments_path)
            if not output_path.is_file():
                raise ModificationFailure(
                    "NATIVE_MESH_MODIFICATION_ARTIFACT_INVALID",
                    "The detached Mesh modification produced no artifact.",
                )
            outputs.append(
                {
                    "index": index,
                    "points": int(result.Mesh.CountPoints),
                    "facets": int(result.Mesh.CountFacets),
                    "segments": segment_count,
                }
            )
        return {"schema": _RESULT_SCHEMA, "ok": True, "outputs": outputs}
    finally:
        App.closeDocument(document.Name)


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The Mesh modification request is not an object.")
    root = Path(str(request.get("workspace") or "")).resolve()
    result_path = _path(request.get("result_path"), root, "result_path")
    try:
        result = execute(request)
    except ModificationFailure as exc:
        result = {
            "schema": _RESULT_SCHEMA,
            "ok": False,
            "failure_code": exc.code,
            "error": str(exc),
        }
    except Exception as exc:
        result = {
            "schema": _RESULT_SCHEMA,
            "ok": False,
            "failure_code": "NATIVE_MESH_MODIFICATION_FAILED",
            "error": "The isolated Mesh modification failed.",
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


if __name__ != "VibeCADMeshModificationChild":
    raise SystemExit(main())
