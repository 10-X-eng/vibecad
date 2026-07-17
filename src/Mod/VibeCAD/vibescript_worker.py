# SPDX-License-Identifier: LGPL-2.1-or-later

"""Headless FreeCAD worker for isolated VibeScript candidate generation.

The worker owns a temporary document.  Model source never receives the user's
live document, and the only data crossing back to the GUI process is validated
JSON plus one native BREP file per declared output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import traceback
from typing import Any


REQUEST_ENV = "VIBECAD_VIBESCRIPT_REQUEST"
RESULT_ENV = "VIBECAD_VIBESCRIPT_RESULT"
SCHEMA = "vibecad-vibescript-worker-v1"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _matrix_values(placement: Any) -> list[float]:
    matrix = placement.toMatrix()
    return [
        float(getattr(matrix, name))
        for name in (
            "A11",
            "A12",
            "A13",
            "A14",
            "A21",
            "A22",
            "A23",
            "A24",
            "A31",
            "A32",
            "A33",
            "A34",
            "A41",
            "A42",
            "A43",
            "A44",
        )
    ]


def _resource_limits(request: dict[str, Any]) -> None:
    try:
        import resource
    except ImportError:
        return

    memory = int(request.get("memory_limit_bytes") or 0)
    cpu = int(request.get("cpu_limit_seconds") or 0)
    output = int(request.get("output_limit_bytes") or 0)

    def apply(resource_id: int, limit: int, label: str) -> None:
        if limit <= 0:
            return
        current_soft, current_hard = resource.getrlimit(resource_id)
        applied = limit if current_hard == resource.RLIM_INFINITY else min(limit, current_hard)
        if applied <= 0:
            raise RuntimeError(f"{label} resource hard limit is {current_hard}.")
        resource.setrlimit(resource_id, (applied, current_hard))

    # macOS rejects RLIMIT_AS for otherwise valid CAD workloads.  The parent
    # process monitors RSS there and on Windows.
    if sys.platform != "darwin":
        apply(resource.RLIMIT_AS, memory, "address-space")
    apply(resource.RLIMIT_CPU, cpu, "CPU")
    apply(resource.RLIMIT_FSIZE, output, "output-file")
    apply(resource.RLIMIT_NOFILE, 64, "open-file")


def _run(request: dict[str, Any], root: Path) -> dict[str, Any]:
    import FreeCAD as App
    import VibeCADVibeScript as vibescript
    import vibescript_api
    import vibescript_executor

    if request.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported VibeScript worker schema: {request.get('schema')!r}.")
    source = str(request.get("source") or "")
    parameters = request.get("parameters")
    expected_outputs = request.get("expected_outputs")
    if not isinstance(parameters, dict):
        raise TypeError("parameters must be an object.")
    if not isinstance(expected_outputs, list) or not all(
        isinstance(item, str) and item for item in expected_outputs
    ):
        raise TypeError("expected_outputs must be a non-empty string list.")

    vibescript.validate_source(source)
    output_directory = root / "outputs"
    output_directory.mkdir(parents=True, exist_ok=False)
    document = App.newDocument("VibeScriptCandidate", "VibeScript Candidate", True, True)
    captured: dict[str, Any] = {}
    try:
        bound_parameters = vibescript_api.Params(
            _binding_name="VibeScriptCandidateParameters", **parameters
        )

        def capture(context: dict[str, Any]) -> None:
            captured.update(context)

        report = vibescript_executor.execute_model(
            document,
            source,
            expected_outputs=expected_outputs,
            parameters=bound_parameters,
            max_operations=int(request.get("max_operations") or 0),
            max_seconds=float(request.get("max_seconds") or 0.0),
            after_contract=capture,
        )
        if not report.get("ok"):
            return report

        result = captured.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("VibeScript executor returned no output-object mapping.")
        output_facts = {
            str(item.get("key") or ""): dict(item.get("shape") or {})
            for item in list(report.get("outputs") or [])
            if isinstance(item, dict)
        }
        outputs: list[dict[str, Any]] = []
        for index, key in enumerate(expected_outputs):
            obj = result[key]
            shape = getattr(obj, "Shape", None)
            if shape is None or shape.isNull():
                raise RuntimeError(f"Output {key!r} has no exportable shape.")
            relative_path = Path("outputs") / f"output-{index:03d}.brep"
            output_path = root / relative_path
            shape.exportBrep(str(output_path))
            if not output_path.is_file() or output_path.stat().st_size <= 0:
                raise RuntimeError(f"FreeCAD could not export output {key!r} as BREP.")
            placement = getattr(obj, "getGlobalPlacement", lambda: obj.Placement)()
            outputs.append(
                {
                    "key": key,
                    "object_name": str(getattr(obj, "Name", "") or ""),
                    "label": str(getattr(obj, "Label", "") or key),
                    "type_id": str(getattr(obj, "TypeId", "") or ""),
                    "brep_path": str(relative_path),
                    "shape": output_facts[key],
                    "placement_matrix": _matrix_values(placement),
                }
            )
        return {
            "ok": True,
            "schema": SCHEMA,
            "outputs": outputs,
            "interfaces": dict(report.get("interfaces") or {}),
            "stdout": str(report.get("stdout") or ""),
            "budget": dict(report.get("budget") or {}),
            "created_objects": list(report.get("created_objects") or []),
        }
    finally:
        App.closeDocument(document.Name)


def main() -> int:
    result_path = Path(os.environ[RESULT_ENV]).resolve()
    try:
        request_path = Path(os.environ[REQUEST_ENV]).resolve()
        root = request_path.parent
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise TypeError("VibeScript worker request must be an object.")
        _resource_limits(request)
        payload = _run(request, root)
    except BaseException as exc:
        payload = {
            "ok": False,
            "exception_type": exc.__class__.__name__,
            "exception_kind": "worker_failure",
            "error": str(exc),
            "traceback": traceback.format_exc(limit=40),
            "transaction": {"opened": False, "committed": False, "aborted": False},
        }
    _write_json(result_path, payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
