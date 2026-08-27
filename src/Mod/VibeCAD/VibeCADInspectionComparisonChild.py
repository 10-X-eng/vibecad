# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native Inspection comparison worker."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


_JOB_SCHEMA = "vibecad-inspection-comparison-job-v1"
_RESULT_SCHEMA = "vibecad-inspection-comparison-result-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema") != _JOB_SCHEMA:
        raise RuntimeError("The Inspection comparison request schema is invalid.")
    root = Path(str(request.get("workspace") or "")).resolve()
    if not root.is_dir():
        raise RuntimeError("The Inspection comparison workspace is unavailable.")
    from vibescript_inspection_api import InspectionDomainAPI
    from vibescript_inspection_worker import (
        configure_inspection_references,
        validate_and_build_inspection,
    )

    configure_inspection_references(root, list(request.get("document_references") or []))
    api = InspectionDomainAPI(
        ("comparison", "group", "measurement", "report"),
        ("inspection_group", "inspection_feature", "measurement", "report"),
    )
    value = api.comparison(
        request["actual"],
        request["nominals"],
        search_radius=float(request["search_radius_mm"]),
        tolerance=float(request["tolerance_mm"]),
        require_complete=bool(request["require_complete"]),
        label=str(request["result_label"]),
    )
    import FreeCAD as App

    document = App.newDocument("VibeCADInspectionComparisonWorker")
    try:
        (root / "outputs").mkdir(mode=0o700)
        outputs, _validation = validate_and_build_inspection(
            document,
            {"Result": value},
            [{"name": "Result", "type": "inspection_feature"}],
            root,
        )
        output = outputs[0]
        distance_path = (root / str(output["artifact_path"])).resolve()
        if root not in distance_path.parents or not distance_path.is_file():
            raise RuntimeError("The native Inspection engine returned no distance artifact.")
        data = dict(output["inspection_data"])
        return {
            "schema": _RESULT_SCHEMA,
            "ok": True,
            "distance_path": str(distance_path.relative_to(root)),
            "distance_sha256": _sha256(distance_path),
            "distance_count": int(output["distance_count"]),
            "summary": dict(data["distance_summary"]),
        }
    finally:
        App.closeDocument(document.Name)


def run(request_path: str | Path) -> dict[str, Any]:
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("The Inspection comparison request is not an object.")
    result_path = Path(str(request.get("result_path") or "")).resolve()
    root = Path(str(request.get("workspace") or "")).resolve()
    if result_path.parent != root:
        raise ValueError("The Inspection comparison result path is outside its workspace.")
    try:
        result = execute(request)
    except Exception as exc:
        result = {
            "schema": _RESULT_SCHEMA,
            "ok": False,
            "failure_code": "NATIVE_INSPECTION_COMPARE_FAILED",
            "error": str(exc)[:1000],
            "exception_type": type(exc).__name__,
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


if __name__ != "VibeCADInspectionComparisonChild":
    raise SystemExit(main())
