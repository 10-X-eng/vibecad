# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated build123d runtime, source persistence, and FreeCAD shape bridge."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Callable

from VibeCADPreferences import load_settings
from VibeCADScriptedOwnership import (
    delete_contained_objects,
    delete_owned_model_objects,
    owned_model_objects,
)


BUILD123D_VERSION = "0.11.1"
RUNTIME_SCHEMA = "vibecad-build123d-runtime-v1"
MODEL_SCHEMA = "vibecad-build123d-model-v1"
ATTEMPT_SCHEMA = "vibecad-build123d-attempt-v1"
MAX_SOURCE_BYTES = 512_000
MAX_OUTPUTS = 64
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MEMORY_LIMIT_BYTES = 6 * 1024 * 1024 * 1024
DEFAULT_CPU_LIMIT_SECONDS = 300
DEFAULT_OUTPUT_LIMIT_BYTES = 2 * 1024 * 1024 * 1024

PROP_MODEL_ID = "VibeCADBuild123dModelId"
PROP_SOURCE = "VibeCADBuild123dSource"
PROP_PARAMETERS = "VibeCADBuild123dParameters"
PROP_REVISION = "VibeCADBuild123dRevision"
PROP_RUNTIME_VERSION = "VibeCADBuild123dRuntimeVersion"
PROP_OUTPUTS = "VibeCADBuild123dOutputs"
PROP_INPUTS = "VibeCADBuild123dInputs"
PROP_OUTPUT_KEY = "VibeCADBuild123dOutputKey"

_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_. -]{0,95}$")
_ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MODEL_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "build123d",
        "collections",
        "dataclasses",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "itertools",
        "math",
        "numpy",
        "operator",
        "statistics",
        "typing",
    }
)
_DISALLOWED_CALLS = frozenset(
    {
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
        "__import__",
    }
)
_DISALLOWED_EXPORT_SYMBOLS = frozenset(
    {
        "ExportDXF",
        "ExportSVG",
        "Mesher",
        "export_brep",
        "export_gltf",
        "export_step",
        "export_stl",
    }
)
_DISALLOWED_BUILD123D_SUBMODULES = frozenset({"exporters", "exporters3d", "mesher"})
_runtime_health_cache: dict[str, Any] | None = None


class Build123dFailure(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        self.payload = dict(payload)
        super().__init__(str(payload.get("error") or "build123d operation failed"))


def _failure(
    code: str,
    stage: str,
    error: str,
    *,
    requested: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    required_changes: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    stage_aliases = {
        "runtime": "precondition",
        "source_validation": "schema",
        "document_state": "precondition",
        "source_edit": "schema",
        "input_export": "native_call",
        "execution": "external_process",
        "step_import": "postcondition",
        "commit": "postcondition",
    }
    normalized_stage = stage_aliases.get(stage, stage)
    payload: dict[str, Any] = {
        "ok": False,
        "tool": "build123d",
        "failure_code": code,
        "failure_stage": normalized_stage,
        "build123d_stage": stage,
        "error": str(error),
        "requested": dict(requested or {}),
        "observed": dict(observed or {}),
        "required_changes": list(required_changes or []),
        "retry_same_call": False,
    }
    payload.update(extra)
    return payload


def runtime_root() -> Path:
    return Path(__file__).resolve().parent / "build123d_runtime"


def runtime_manifest_path() -> Path:
    return runtime_root() / "runtime.json"


def _read_runtime_manifest() -> dict[str, Any]:
    path = runtime_manifest_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"build123d runtime manifest could not be read: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != RUNTIME_SCHEMA:
        raise RuntimeError(f"build123d runtime manifest is invalid: {path}")
    return payload


def _manifest_path(manifest: dict[str, Any], field: str, *, base: Path) -> Path:
    value = str(manifest.get(field) or "").strip()
    if not value:
        raise RuntimeError(f"build123d runtime manifest is missing {field}.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def runtime_health(*, refresh: bool = False) -> dict[str, Any]:
    global _runtime_health_cache
    if _runtime_health_cache is not None and not refresh:
        return dict(_runtime_health_cache)
    try:
        root = runtime_root()
        manifest = _read_runtime_manifest()
        version = str(manifest.get("version") or "")
        if version != BUILD123D_VERSION:
            raise RuntimeError(
                f"runtime contains build123d {version or 'unknown'}; "
                f"VibeCAD requires {BUILD123D_VERSION}."
            )
        site_packages = _manifest_path(manifest, "site_packages", base=root)
        python_prefix = _manifest_path(manifest, "python_prefix", base=root)
        executable_value = str(manifest.get("python_executable") or "").strip()
        if not executable_value:
            raise RuntimeError("build123d runtime manifest is missing python_executable.")
        python_executable = (python_prefix / executable_value).resolve()
        if not site_packages.is_dir():
            raise RuntimeError(f"build123d site-packages is missing: {site_packages}")
        if not python_executable.is_file():
            raise RuntimeError(f"build123d Python executable is missing: {python_executable}")
        distribution = site_packages / f"build123d-{BUILD123D_VERSION}.dist-info"
        if not distribution.is_dir():
            raise RuntimeError(
                f"build123d {BUILD123D_VERSION} distribution metadata is missing."
            )
        if not (
            sys.platform.startswith("linux")
            or sys.platform in {"darwin", "win32"}
        ):
            raise RuntimeError(
                f"build123d isolated execution is not implemented on {sys.platform}."
            )
        _runtime_health_cache = {
            "ready": True,
            "version": version,
            "runtime_root": str(root),
            "site_packages": str(site_packages),
            "python_prefix": str(python_prefix),
            "python_executable": str(python_executable),
            "python_relative": executable_value,
            "isolation": "python-isolated-process",
            "manifest": manifest,
            "error": None,
        }
    except Exception as exc:
        _runtime_health_cache = {
            "ready": False,
            "version": BUILD123D_VERSION,
            "isolation": None,
            "error": str(exc),
        }
    return dict(_runtime_health_cache)


def validate_source(source: str) -> None:
    encoded = str(source or "").encode("utf-8")
    if not encoded:
        raise Build123dFailure(
            _failure("SOURCE_REQUIRED", "source_validation", "source is required.")
        )
    if len(encoded) > MAX_SOURCE_BYTES:
        raise Build123dFailure(
            _failure(
                "SOURCE_TOO_LARGE",
                "source_validation",
                f"source exceeds {MAX_SOURCE_BYTES} UTF-8 bytes.",
                observed={"source_bytes": len(encoded)},
            )
        )
    try:
        tree = ast.parse(source, filename="<vibecad-build123d>", mode="exec")
    except SyntaxError as exc:
        raise Build123dFailure(
            _failure(
                "SOURCE_SYNTAX_ERROR",
                "source_validation",
                str(exc),
                observed={"line": exc.lineno, "column": exc.offset},
            )
        ) from exc
    violations: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".", 1)[0] for alias in node.names]
            denied = [root for root in roots if root not in _ALLOWED_IMPORT_ROOTS]
            if denied:
                violations.append(
                    {"line": node.lineno, "reason": f"imports not allowed: {denied}"}
                )
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "build123d" and any(
                    part in _DISALLOWED_BUILD123D_SUBMODULES for part in parts[1:]
                ):
                    violations.append(
                        {
                            "line": node.lineno,
                            "reason": f"exporter module not allowed: {alias.name}",
                        }
                    )
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            parts = module.split(".")
            root = parts[0]
            if root not in _ALLOWED_IMPORT_ROOTS:
                violations.append(
                    {"line": node.lineno, "reason": f"import not allowed: {root}"}
                )
            elif root == "build123d":
                if any(part in _DISALLOWED_BUILD123D_SUBMODULES for part in parts[1:]):
                    violations.append(
                        {
                            "line": node.lineno,
                            "reason": f"exporter module not allowed: {module}",
                        }
                    )
                for alias in node.names:
                    if alias.name in _DISALLOWED_EXPORT_SYMBOLS:
                        violations.append(
                            {
                                "line": node.lineno,
                                "reason": (
                                    f"exporter import not allowed: {alias.name}"
                                ),
                            }
                        )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DISALLOWED_CALLS:
                violations.append(
                    {"line": node.lineno, "reason": f"call not allowed: {node.func.id}"}
                )
        elif isinstance(node, ast.Name) and node.id in _DISALLOWED_EXPORT_SYMBOLS:
            violations.append(
                {"line": node.lineno, "reason": f"exporter access not allowed: {node.id}"}
            )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": f"dunder access not allowed: {node.attr}",
                    }
                )
            elif node.attr in _DISALLOWED_EXPORT_SYMBOLS:
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": f"exporter access not allowed: {node.attr}",
                    }
                )
    if violations:
        raise Build123dFailure(
            _failure(
                "SOURCE_POLICY_VIOLATION",
                "source_validation",
                "build123d source violates the isolated execution policy.",
                observed={"violations": violations[:20]},
                required_changes=[{"remove_policy_violations": violations[:20]}],
            )
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def source_revision(
    source: str,
    parameters: dict[str, Any],
    inputs: dict[str, str],
    expected_outputs: list[str],
) -> str:
    payload = {
        "schema": MODEL_SCHEMA,
        "runtime_version": BUILD123D_VERSION,
        "source": source,
        "parameters": parameters,
        "inputs": inputs,
        "expected_outputs": expected_outputs,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _model_objects(doc: Any) -> list[Any]:
    result: list[Any] = []
    for obj in list(getattr(doc, "Objects", []) or []):
        if str(getattr(obj, "TypeId", "") or "") != "App::Part":
            continue
        model_id = str(getattr(obj, PROP_MODEL_ID, "") or "")
        properties = set(getattr(obj, "PropertiesList", []) or [])
        if (
            _MODEL_ID_PATTERN.fullmatch(model_id)
            and PROP_SOURCE in properties
            and PROP_REVISION in properties
        ):
            result.append(obj)
    return result


def _find_model(doc: Any, model_id: str) -> Any | None:
    clean = str(model_id or "").strip().lower()
    if not clean:
        return None
    if not _MODEL_ID_PATTERN.fullmatch(clean):
        raise Build123dFailure(
            _failure(
                "INVALID_MODEL_ID",
                "precondition",
                "target_model_id must be a 32-character lowercase hexadecimal id.",
                requested={"target_model_id": model_id},
            )
        )
    matches = [obj for obj in _model_objects(doc) if getattr(obj, PROP_MODEL_ID) == clean]
    if len(matches) > 1:
        raise Build123dFailure(
            _failure(
                "DUPLICATE_MODEL_ID",
                "document_state",
                f"Multiple FreeCAD objects claim build123d model id {clean}.",
                observed={"objects": [obj.Name for obj in matches]},
            )
        )
    return matches[0] if matches else None


def _model_summary(container: Any, *, include_source: bool) -> dict[str, Any]:
    outputs_raw = str(getattr(container, PROP_OUTPUTS, "{}") or "{}")
    inputs_raw = str(getattr(container, PROP_INPUTS, "{}") or "{}")
    parameters_raw = str(getattr(container, PROP_PARAMETERS, "{}") or "{}")
    try:
        outputs = json.loads(outputs_raw)
    except ValueError:
        outputs = {"invalid_json": outputs_raw}
    try:
        inputs = json.loads(inputs_raw)
    except ValueError:
        inputs = {"invalid_json": inputs_raw}
    try:
        parameters = json.loads(parameters_raw)
    except ValueError:
        parameters = {"invalid_json": parameters_raw}
    summary = {
        "model_id": str(getattr(container, PROP_MODEL_ID, "") or ""),
        "object_name": str(getattr(container, "Name", "") or ""),
        "label": str(getattr(container, "Label", "") or ""),
        "revision": str(getattr(container, PROP_REVISION, "") or ""),
        "runtime_version": str(getattr(container, PROP_RUNTIME_VERSION, "") or ""),
        "parameters": parameters,
        "inputs": inputs,
        "outputs": outputs,
    }
    if include_source:
        summary["source"] = str(getattr(container, PROP_SOURCE, "") or "")
    return summary


def _project_root(service: Any) -> Path:
    value = str(service.project_context().get("root") or "").strip()
    if not value:
        raise Build123dFailure(
            _failure(
                "PROJECT_ROOT_UNAVAILABLE",
                "precondition",
                "Project root is unavailable.",
            )
        )
    return Path(value)


def _model_directory(project_root: str | Path, model_id: str) -> Path:
    return Path(project_root) / "build123d" / model_id


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Build123dFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"Persisted build123d {label} is invalid: {exc}",
                observed={"path": str(path)},
            )
        ) from exc
    if not isinstance(payload, dict):
        raise Build123dFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"Persisted build123d {label} must be a JSON object.",
                observed={"path": str(path)},
            )
        )
    return payload


def _artifact_contract(project_root: str | Path, model_id: str) -> dict[str, Any] | None:
    directory = _model_directory(project_root, model_id)
    manifest_path = directory / "manifest.json"
    source_path = directory / "model.py"
    parameters_path = directory / "parameters.json"
    if not directory.is_dir():
        return None
    if not manifest_path.is_file() or not source_path.is_file() or not parameters_path.is_file():
        raise Build123dFailure(
            _failure(
                "MODEL_ARTIFACT_INCOMPLETE",
                "document_state",
                "The persisted build123d model is missing its manifest, source, or parameters.",
                observed={"artifact_directory": str(directory)},
            )
        )
    manifest = _read_json_object(manifest_path, "manifest")
    if manifest.get("schema") != MODEL_SCHEMA or str(manifest.get("model_id") or "") != model_id:
        raise Build123dFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                "The persisted build123d manifest identity is invalid.",
                observed={"path": str(manifest_path), "manifest": manifest},
            )
        )
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Build123dFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"Persisted build123d source could not be read: {exc}",
                observed={"path": str(source_path)},
            )
        ) from exc
    parameters = _read_json_object(parameters_path, "parameters")
    inputs = _clean_inputs(manifest.get("inputs") or {})
    output_map = manifest.get("outputs") or {}
    if not isinstance(output_map, dict):
        raise Build123dFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                "Persisted build123d outputs must be an object.",
                observed={"path": str(manifest_path)},
            )
        )
    output_facts = manifest.get("output_facts") or {}
    if not isinstance(output_facts, dict):
        raise Build123dFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                "Persisted build123d output facts must be an object.",
                observed={"path": str(manifest_path)},
            )
        )
    expected_outputs = manifest.get("expected_outputs")
    if expected_outputs is None:
        expected_outputs = list(output_map)
    expected_outputs = _clean_outputs(expected_outputs)
    working_revision = str(
        manifest.get("working_revision") or manifest.get("revision") or ""
    )
    calculated_revision = source_revision(source, parameters, inputs, expected_outputs)
    if working_revision != calculated_revision:
        raise Build123dFailure(
            _failure(
                "MODEL_ARTIFACT_REVISION_MISMATCH",
                "document_state",
                "Persisted build123d source and metadata do not match the working revision.",
                observed={
                    "manifest_revision": working_revision,
                    "calculated_revision": calculated_revision,
                    "artifact_directory": str(directory),
                },
            )
        )
    state = str(manifest.get("state") or "accepted")
    accepted_revision = str(
        manifest.get("accepted_revision")
        or (working_revision if state == "accepted" else "")
    )
    return {
        "model_id": model_id,
        "model_name": str(manifest.get("label") or ""),
        "source": source,
        "parameters": parameters,
        "input_objects": inputs,
        "expected_outputs": expected_outputs,
        "outputs": output_map,
        "output_facts": output_facts,
        "working_revision": working_revision,
        "accepted_revision": accepted_revision,
        "state": state,
        "latest_attempt": manifest.get("latest_attempt") or {},
        "directory": directory,
        "manifest": manifest,
    }


def _artifact_summary(contract: dict[str, Any], *, include_source: bool) -> dict[str, Any]:
    summary = {
        "model_id": contract["model_id"],
        "object_name": "",
        "label": contract["model_name"],
        "revision": contract["working_revision"],
        "working_revision": contract["working_revision"],
        "accepted_revision": contract["accepted_revision"],
        "state": contract["state"],
        "parameters": contract["parameters"],
        "inputs": contract["input_objects"],
        "outputs": contract["outputs"],
    }
    if include_source:
        summary["source"] = contract["source"]
    return summary


def editor_model_index_snapshot(doc: Any) -> dict[str, Any]:
    """Capture native model metadata without filesystem or Shape access."""

    return {
        "native_models": [
            _model_summary(obj, include_source=False) for obj in _model_objects(doc)
        ]
    }


def complete_editor_model_index(
    snapshot: dict[str, Any],
    project_root: str | Path | None,
) -> list[dict[str, Any]]:
    """Complete a model selector index using project artifacts off the GUI thread."""

    summaries = {
        str(item.get("model_id") or ""): dict(item)
        for item in list(snapshot.get("native_models") or [])
        if isinstance(item, dict) and str(item.get("model_id") or "")
    }
    root = Path(project_root) if project_root else None
    build_root = root / "build123d" if root else None
    if build_root is not None and build_root.is_dir():
        for directory in sorted(build_root.iterdir()):
            if not directory.is_dir() or not _MODEL_ID_PATTERN.fullmatch(directory.name):
                continue
            contract = _artifact_contract(root, directory.name)
            if contract is None:
                continue
            summary = _artifact_summary(contract, include_source=False)
            native = summaries.get(directory.name)
            if native is not None:
                summary["object_name"] = native.get("object_name", "")
                summary["outputs"] = native.get("outputs", summary["outputs"])
            summaries[directory.name] = summary
    return list(summaries.values())


def model_summaries(
    doc: Any,
    project_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    return complete_editor_model_index(editor_model_index_snapshot(doc), project_root)


def editor_model_inspection_snapshot(service: Any, model_id: str) -> dict[str, Any]:
    """Capture one model's native properties without inspecting its geometry."""

    doc = service._active_document()
    if doc is None:
        return _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
    try:
        project_root = _project_root(service)
        container = _find_model(doc, model_id)
    except Build123dFailure as exc:
        return exc.payload
    return {
        "ok": True,
        "model_id": str(model_id),
        "project_root": str(project_root),
        "native_model": (
            _model_summary(container, include_source=True)
            if container is not None
            else None
        ),
    }


def complete_editor_model_inspection(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Read one model's source contract away from the GUI thread."""

    if snapshot.get("ok") is not True:
        return dict(snapshot)
    model_id = str(snapshot.get("model_id") or "")
    project_root = str(snapshot.get("project_root") or "")
    native = snapshot.get("native_model")
    try:
        contract = _artifact_contract(project_root, model_id)
    except Build123dFailure as exc:
        return exc.payload
    if not isinstance(native, dict) and contract is None:
        return _failure(
            "MODEL_NOT_FOUND",
            "precondition",
            f"No build123d model has id {model_id!r}.",
        )
    if contract is None:
        model = dict(native)
        model.update(
            {
                "working_revision": model["revision"],
                "accepted_revision": model["revision"],
                "state": "accepted",
            }
        )
    else:
        model = _artifact_summary(contract, include_source=True)
    if isinstance(native, dict):
        model["object_name"] = str(native.get("object_name") or "")
    model["artifact_directory"] = str(_model_directory(project_root, model_id))
    if contract is not None:
        latest_attempt = _inspect_latest_attempt(contract)
        if latest_attempt:
            model["latest_attempt"] = latest_attempt
        accepted_revision = contract["accepted_revision"]
        if accepted_revision and accepted_revision != contract["working_revision"]:
            accepted_path = contract["directory"] / "revisions" / f"{accepted_revision}.py"
            if accepted_path.is_file():
                model["accepted_source"] = accepted_path.read_text(encoding="utf-8")
    return {
        "ok": True,
        "model": model,
    }


def inspect_model(service: Any, model_id: str) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
    try:
        project_root = _project_root(service)
        contract = _artifact_contract(project_root, model_id)
        container = _find_model(doc, model_id)
    except Build123dFailure as exc:
        return exc.payload
    if container is None and contract is None:
        return _failure(
            "MODEL_NOT_FOUND",
            "precondition",
            f"No build123d model has id {model_id!r}.",
            observed={"available_models": model_summaries(doc, project_root)},
        )
    if contract is None:
        model = _model_summary(container, include_source=True)
        model.update(
            {
                "working_revision": model["revision"],
                "accepted_revision": model["revision"],
                "state": "accepted",
            }
        )
    else:
        model = _artifact_summary(contract, include_source=True)
    if container is not None:
        model["object_name"] = container.Name
        output_geometry: list[dict[str, Any]] = []
        for key, (body, feature) in _output_objects(container).items():
            item = {
                "key": key,
                "body": body.Name,
                "feature": feature.Name,
                "shape": _freecad_shape_facts(feature.Shape),
            }
            if contract is not None:
                persisted_facts = contract["output_facts"].get(key)
                if isinstance(persisted_facts, dict):
                    item.update(persisted_facts)
            output_geometry.append(item)
        model["accepted_outputs"] = output_geometry
    model["artifact_directory"] = str(_model_directory(project_root, model_id))
    if contract is not None:
        latest_attempt = _inspect_latest_attempt(contract)
        if latest_attempt:
            model["latest_attempt"] = latest_attempt
        accepted_revision = contract["accepted_revision"]
        if accepted_revision and accepted_revision != contract["working_revision"]:
            accepted_path = contract["directory"] / "revisions" / f"{accepted_revision}.py"
            if accepted_path.is_file():
                model["accepted_source"] = accepted_path.read_text(encoding="utf-8")
    return {
        "ok": True,
        "model": model,
        "cad_revision": service.structural_document_revision(),
    }


def delete_model(
    service: Any,
    model_id: str,
    expected_revision: str,
    reason: str,
) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
    try:
        project_root = _project_root(service)
        contract = _artifact_contract(project_root, model_id)
        container = _find_model(doc, model_id)
    except Build123dFailure as exc:
        return exc.payload
    if container is None and contract is None:
        return _failure(
            "MODEL_NOT_FOUND",
            "precondition",
            f"No build123d model has id {model_id!r}.",
            observed={"available_models": model_summaries(doc, project_root)},
        )
    current_revision = (
        contract["working_revision"]
        if contract is not None
        else str(getattr(container, PROP_REVISION, "") or "")
    )
    if str(expected_revision or "").strip() != current_revision:
        return _failure(
            "STALE_MODEL_REVISION",
            "precondition",
            "The build123d model changed after it was inspected.",
            requested={"expected_revision": expected_revision},
            observed={"current_revision": current_revision},
            required_changes=[{"inspect_model": model_id}],
        )
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return _failure("DELETE_REASON_REQUIRED", "schema", "reason cannot be empty.")
    artifact_directory = _model_directory(project_root, model_id)
    if not artifact_directory.is_dir():
        return _failure(
            "MODEL_ARTIFACT_MISSING",
            "precondition",
            "The persisted build123d artifact directory is missing; deletion was not started.",
            observed={"artifact_directory": str(artifact_directory)},
        )
    deleted_objects: list[str] = []
    if container is not None:
        doc.openTransaction("Delete build123d model")
        try:
            deleted_objects = delete_owned_model_objects(doc, PROP_MODEL_ID, model_id)
            doc.recompute()
            remaining = sorted(
                {
                    name
                    for name in deleted_objects
                    if doc.getObject(name) is not None
                }
                | {
                    str(obj.Name)
                    for obj in owned_model_objects(doc, PROP_MODEL_ID, model_id)
                }
            )
            if remaining:
                raise RuntimeError(
                    "FreeCAD retained model-owned objects after deletion: "
                    + ", ".join(remaining)
                )
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            return _failure(
                "DELETE_FAILED",
                "commit",
                f"build123d model deletion failed: {exc}",
            )
    shutil.rmtree(artifact_directory)
    return {
        "ok": True,
        "deleted_model_id": model_id,
        "deleted_revision": current_revision,
        "reason": clean_reason,
        "deleted_objects": deleted_objects,
        "artifact_directory": str(artifact_directory),
        "cad_revision": service.structural_document_revision(),
    }


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Build123dFailure(
            _failure(f"INVALID_{label.upper()}", "schema", f"{label} must be an object.")
        )
    try:
        encoded = _canonical_json(value)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise Build123dFailure(
            _failure(
                f"INVALID_{label.upper()}",
                "schema",
                f"{label} is not JSON-safe: {exc}",
            )
        ) from exc
    return decoded


def _clean_inputs(value: Any) -> dict[str, str]:
    raw = _json_object(value, "input_objects")
    cleaned: dict[str, str] = {}
    for alias, object_name in raw.items():
        clean_alias = str(alias or "").strip()
        clean_name = str(object_name or "").strip()
        if not _ALIAS_PATTERN.fullmatch(clean_alias) or not clean_name:
            raise Build123dFailure(
                _failure(
                    "INVALID_INPUT_OBJECTS",
                    "schema",
                    "Each input alias must be a Python identifier-like name and "
                    "each value must be an exact FreeCAD object name.",
                    observed={"alias": alias, "object_name": object_name},
                )
            )
        cleaned[clean_alias] = clean_name
    return cleaned


def _clean_outputs(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise Build123dFailure(
            _failure(
                "OUTPUTS_REQUIRED",
                "schema",
                "expected_outputs must contain at least one output key.",
            )
        )
    if len(value) > MAX_OUTPUTS:
        raise Build123dFailure(
            _failure(
                "TOO_MANY_OUTPUTS",
                "schema",
                f"expected_outputs may contain at most {MAX_OUTPUTS} keys.",
            )
        )
    cleaned = [str(item or "").strip() for item in value]
    if any(not _NAME_PATTERN.fullmatch(item) for item in cleaned):
        raise Build123dFailure(
            _failure(
                "INVALID_OUTPUT_NAME",
                "schema",
                "Every output key must start with a letter and contain only "
                "letters, numbers, spaces, dots, underscores, or hyphens.",
                observed={"expected_outputs": cleaned},
            )
        )
    if len(set(cleaned)) != len(cleaned):
        raise Build123dFailure(
            _failure(
                "DUPLICATE_OUTPUT_NAME",
                "schema",
                "expected_outputs contains duplicate keys.",
                observed={"expected_outputs": cleaned},
            )
        )
    return cleaned


def _model_contract(container: Any) -> dict[str, Any]:
    try:
        parameters = json.loads(str(getattr(container, PROP_PARAMETERS) or "{}"))
        inputs = json.loads(str(getattr(container, PROP_INPUTS) or "{}"))
        output_map = json.loads(str(getattr(container, PROP_OUTPUTS) or "{}"))
    except (TypeError, ValueError) as exc:
        raise Build123dFailure(
            _failure(
                "MODEL_METADATA_INVALID",
                "document_state",
                f"Persisted build123d model metadata is invalid: {exc}",
                observed={"model_id": str(getattr(container, PROP_MODEL_ID, "") or "")},
            )
        ) from exc
    if not isinstance(parameters, dict) or not isinstance(inputs, dict) or not isinstance(output_map, dict):
        raise Build123dFailure(
            _failure(
                "MODEL_METADATA_INVALID",
                "document_state",
                "Persisted parameters, inputs, and outputs must all be JSON objects.",
            )
        )
    source = str(getattr(container, PROP_SOURCE, "") or "")
    validate_source(source)
    return {
        "model_name": str(getattr(container, "Label", "") or ""),
        "source": source,
        "parameters": _json_object(parameters, "parameters"),
        "input_objects": _clean_inputs(inputs),
        "expected_outputs": _clean_outputs(list(output_map)),
    }


def _apply_source_edits(source: str, edits: Any) -> str:
    if not isinstance(edits, list) or not edits:
        raise Build123dFailure(
            _failure("SOURCE_EDITS_REQUIRED", "schema", "edits must contain at least one replacement.")
        )
    candidate = source
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise Build123dFailure(
                _failure(
                    "INVALID_SOURCE_EDIT",
                    "schema",
                    f"Source edit {index} must be an object.",
                )
            )
        old_text = str(edit.get("old_text") or "")
        new_text = str(edit.get("new_text") or "")
        if not old_text:
            raise Build123dFailure(
                _failure(
                    "INVALID_SOURCE_EDIT",
                    "schema",
                    f"Source edit {index} has empty old_text.",
                )
            )
        occurrences = candidate.count(old_text)
        if occurrences != 1:
            raise Build123dFailure(
                _failure(
                    "SOURCE_EDIT_NOT_UNIQUE",
                    "source_edit",
                    f"Source edit {index} old_text matched {occurrences} times; expected exactly once.",
                    observed={
                        "edit_index": index,
                        "match_count": occurrences,
                        "old_text": old_text,
                    },
                    required_changes=[{"inspect_model_and_correct_old_text": index}],
                )
            )
        candidate = candidate.replace(old_text, new_text, 1)
    return candidate


def _merge_patch(target: Any, patch: Any) -> Any:
    if not isinstance(patch, dict):
        return json.loads(_canonical_json(patch))
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(str(key), None)
        else:
            result[str(key)] = _merge_patch(result.get(str(key)), value)
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _persist_working_candidate(prepared: dict[str, Any]) -> dict[str, str]:
    directory = _model_directory(prepared["project_root"], prepared["model_id"])
    attempts = directory / "attempts"
    attempt = attempts / prepared["revision"]
    directory.mkdir(parents=True, exist_ok=True)
    attempt.mkdir(parents=True, exist_ok=True)
    _write_text(directory / "model.py", prepared["source"])
    _write_json(directory / "parameters.json", prepared["parameters"])
    _write_text(attempt / "model.py", prepared["source"])
    _write_json(attempt / "parameters.json", prepared["parameters"])
    attempt_manifest = {
        "schema": ATTEMPT_SCHEMA,
        "model_id": prepared["model_id"],
        "label": prepared["model_name"],
        "operation": prepared["operation"],
        "revision": prepared["revision"],
        "base_revision": prepared["base_revision"],
        "accepted_revision": prepared["accepted_revision_before"],
        "runtime_version": BUILD123D_VERSION,
        "inputs": prepared["input_objects"],
        "expected_outputs": prepared["expected_outputs"],
        "status": "running",
    }
    _write_json(attempt / "manifest.json", attempt_manifest)
    manifest = {
        "schema": MODEL_SCHEMA,
        "model_id": prepared["model_id"],
        "label": prepared["model_name"],
        "state": "running",
        "revision": prepared["revision"],
        "working_revision": prepared["revision"],
        "accepted_revision": prepared["accepted_revision_before"],
        "runtime_version": BUILD123D_VERSION,
        "inputs": prepared["input_objects"],
        "expected_outputs": prepared["expected_outputs"],
        "outputs": prepared["accepted_outputs"],
        "output_facts": prepared["accepted_output_facts"],
        "latest_attempt": {
            "revision": prepared["revision"],
            "status": "running",
            "path": str(Path("attempts") / prepared["revision"]),
        },
    }
    _write_json(directory / "manifest.json", manifest)
    return {
        "artifact_directory": str(directory),
        "attempt_directory": str(attempt),
    }


def stage_editor_source(
    service: Any,
    model_id: str,
    expected_revision: str,
    source: str,
) -> dict[str, Any]:
    """Persist a human-edited working source revision without accepting geometry."""
    project_root = _project_root(service)
    directory = _model_directory(project_root, model_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise Build123dFailure(
            _failure("MODEL_NOT_FOUND", "precondition", f"No build123d model has id {model_id!r}.")
        )
    manifest = _read_json_object(manifest_path, "model manifest")
    current_revision = str(manifest.get("working_revision") or manifest.get("revision") or "")
    if current_revision != str(expected_revision or ""):
        raise Build123dFailure(
            _failure(
                "STALE_MODEL_REVISION",
                "precondition",
                "The build123d source changed after the editor loaded it.",
                requested={"expected_revision": expected_revision},
                observed={"current_revision": current_revision},
            )
        )
    validate_source(source)
    parameters = _read_json_object(directory / "parameters.json", "parameters")
    inputs = _clean_inputs(manifest.get("inputs") or {})
    expected_outputs = _clean_outputs(manifest.get("expected_outputs") or list((manifest.get("outputs") or {}).keys()))
    revision = source_revision(source, parameters, inputs, expected_outputs)
    if revision == current_revision:
        return {"ok": True, "changed": False, "working_revision": revision}
    _write_text(directory / "model.py", source)
    manifest.update(
        {
            "state": "working",
            "revision": revision,
            "working_revision": revision,
            "latest_attempt": {
                "revision": revision,
                "status": "working",
                "path": str(Path("attempts") / revision),
            },
        }
    )
    _write_json(manifest_path, manifest)
    attempt = directory / "attempts" / revision
    attempt.mkdir(parents=True, exist_ok=True)
    _write_text(attempt / "model.py", source)
    _write_json(attempt / "parameters.json", parameters)
    _write_json(
        attempt / "manifest.json",
        {
            "schema": ATTEMPT_SCHEMA,
            "model_id": model_id,
            "revision": revision,
            "status": "working",
            "created_at": time.time(),
        },
    )
    return {"ok": True, "changed": True, "working_revision": revision}


def revert_working_to_accepted(service: Any, model_id: str) -> dict[str, Any]:
    project_root = _project_root(service)
    contract = _artifact_contract(project_root, model_id)
    if contract is None:
        raise Build123dFailure(
            _failure("MODEL_NOT_FOUND", "precondition", f"No build123d model has id {model_id!r}.")
        )
    accepted = contract["accepted_revision"]
    if not accepted:
        raise Build123dFailure(
            _failure("NO_ACCEPTED_REVISION", "precondition", "This build123d model has no accepted revision to restore.")
        )
    directory = contract["directory"]
    source_path = directory / "revisions" / f"{accepted}.py"
    parameters_path = directory / "revisions" / f"{accepted}.parameters.json"
    if not source_path.is_file() or not parameters_path.is_file():
        raise Build123dFailure(
            _failure("ACCEPTED_REVISION_MISSING", "document_state", "The accepted build123d revision files are missing.")
        )
    source = source_path.read_text(encoding="utf-8")
    parameters = _read_json_object(parameters_path, "accepted parameters")
    _write_text(directory / "model.py", source)
    _write_json(directory / "parameters.json", parameters)
    manifest = dict(contract["manifest"])
    manifest.update(
        {
            "state": "accepted",
            "revision": accepted,
            "working_revision": accepted,
            "latest_attempt": {
                "revision": accepted,
                "status": "accepted",
                "path": str(Path("attempts") / accepted),
            },
        }
    )
    _write_json(directory / "manifest.json", manifest)
    return {"ok": True, "model_id": model_id, "working_revision": accepted, "source": source}


def record_failed_attempt(
    prepared: dict[str, Any],
    failure: dict[str, Any],
) -> dict[str, Any]:
    paths = _persist_working_candidate(prepared)
    directory = Path(paths["artifact_directory"])
    attempt = Path(paths["attempt_directory"])
    staging = Path(prepared["staging"])
    result_path = staging / "result.json"
    if result_path.is_file():
        shutil.copy2(result_path, attempt / "runner-result.json")
    retained_steps: list[str] = []
    source_outputs = staging / "outputs"
    retained_output_directory = attempt / "outputs"
    if source_outputs.is_dir():
        for source in sorted(source_outputs.glob("*.step")):
            retained_output_directory.mkdir(parents=True, exist_ok=True)
            target = retained_output_directory / source.name
            shutil.copy2(source, target)
            retained_steps.append(str(target))
    stored_failure = dict(failure)
    stored_failure.pop("requested", None)
    _write_json(attempt / "failure.json", stored_failure)
    attempt_manifest = _read_json_object(attempt / "manifest.json", "attempt manifest")
    attempt_manifest.update(
        {
            "status": "failed",
            "failure_code": str(failure.get("failure_code") or "BUILD123D_FAILED"),
            "failure_stage": str(failure.get("failure_stage") or "external_process"),
            "retained_step_files": [Path(path).name for path in retained_steps],
        }
    )
    _write_json(attempt / "manifest.json", attempt_manifest)
    manifest = _read_json_object(directory / "manifest.json", "manifest")
    state = (
        "candidate_failed"
        if str(prepared.get("accepted_revision_before") or "")
        else "draft_failed"
    )
    manifest.update(
        {
            "state": state,
            "revision": prepared["revision"],
            "working_revision": prepared["revision"],
            "accepted_revision": prepared["accepted_revision_before"],
            "latest_attempt": {
                "revision": prepared["revision"],
                "status": "failed",
                "failure_code": attempt_manifest["failure_code"],
                "failure_stage": attempt_manifest["failure_stage"],
                "path": str(Path("attempts") / prepared["revision"]),
            },
        }
    )
    _write_json(directory / "manifest.json", manifest)
    return {
        "model_id": prepared["model_id"],
        "state": state,
        "working_revision": prepared["revision"],
        "accepted_revision": prepared["accepted_revision_before"],
        "artifact_directory": str(directory),
        "attempt_directory": str(attempt),
        "retained_step_files": retained_steps,
    }


def _inspect_latest_attempt(contract: dict[str, Any]) -> dict[str, Any]:
    latest = contract.get("latest_attempt") or {}
    relative = str(latest.get("path") or "").strip()
    if not relative:
        return {}
    directory = (contract["directory"] / relative).resolve()
    if contract["directory"].resolve() not in directory.parents or not directory.is_dir():
        return {
            "status": "invalid_artifact",
            "error": "Latest attempt path is missing or outside the model artifact directory.",
        }
    response = dict(latest)
    response["directory"] = str(directory)
    failure_path = directory / "failure.json"
    if failure_path.is_file():
        response["failure"] = _read_json_object(failure_path, "attempt failure")
    manifest_path = directory / "manifest.json"
    manifest = (
        _read_json_object(manifest_path, "attempt manifest")
        if manifest_path.is_file()
        else {}
    )
    expected_outputs = list(manifest.get("expected_outputs") or [])
    candidate_outputs: list[dict[str, Any]] = []
    for index, key in enumerate(expected_outputs):
        path = directory / "outputs" / f"{index:03d}.step"
        if not path.is_file():
            continue
        item: dict[str, Any] = {"key": str(key), "step_file": str(path)}
        try:
            import Part

            item["shape"] = _freecad_shape_facts(Part.read(str(path)))
        except Exception as exc:
            item["import_error"] = str(exc)
        candidate_outputs.append(item)
    if candidate_outputs:
        response["candidate_outputs"] = candidate_outputs
    return response


def prepare_execution(
    service: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    health = runtime_health()
    if not health.get("ready"):
        raise Build123dFailure(
            _failure(
                "RUNTIME_UNAVAILABLE",
                "runtime",
                str(health.get("error") or "build123d runtime is unavailable."),
                observed=health,
            )
        )
    doc = service._active_document()
    if doc is None:
        raise Build123dFailure(
            _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
        )
    project_root = _project_root(service)
    operation = str(tool_name or "").strip()
    creating = operation == "build123d.create_model"
    if creating:
        model_name = str(arguments.get("model_name") or "").strip()
        if not _NAME_PATTERN.fullmatch(model_name):
            raise Build123dFailure(
                _failure(
                    "INVALID_MODEL_NAME",
                    "schema",
                    "model_name must start with a letter and contain at most 96 "
                    "letters, numbers, spaces, dots, underscores, or hyphens.",
                )
            )
        source = str(arguments.get("source") or "")
        parameters = _json_object(arguments.get("parameters"), "parameters")
        input_objects = _clean_inputs(arguments.get("input_objects"))
        expected_outputs = _clean_outputs(arguments.get("expected_outputs"))
        validate_source(source)
        duplicates = [
            item
            for item in model_summaries(doc, project_root)
            if str(item.get("label") or "") == model_name
        ]
        if duplicates:
            raise Build123dFailure(
                _failure(
                    "MODEL_NAME_EXISTS",
                    "precondition",
                    "A build123d model with this label already exists; inspect and "
                    "update it by model id instead of creating a duplicate.",
                    observed={"matches": duplicates},
                )
            )
        model_id = uuid.uuid4().hex
        target = None
        base_revision = ""
        accepted_revision_before = ""
        accepted_outputs: dict[str, Any] = {}
        accepted_output_facts: dict[str, Any] = {}
    else:
        model_id = str(arguments.get("model_id") or "").strip().lower()
        target = _find_model(doc, model_id)
        artifact = _artifact_contract(project_root, model_id)
        if target is None and artifact is None:
            raise Build123dFailure(
                _failure(
                    "MODEL_NOT_FOUND",
                    "precondition",
                    f"No build123d model has id {model_id!r}.",
                    observed={"available_models": model_summaries(doc, project_root)},
                )
            )
        if artifact is not None:
            current = {
                "model_name": artifact["model_name"],
                "source": artifact["source"],
                "parameters": artifact["parameters"],
                "input_objects": artifact["input_objects"],
                "expected_outputs": artifact["expected_outputs"],
            }
            base_revision = artifact["working_revision"]
            accepted_revision_before = artifact["accepted_revision"]
            accepted_outputs = artifact["outputs"]
            accepted_output_facts = artifact["output_facts"]
            if accepted_revision_before and target is None:
                raise Build123dFailure(
                    _failure(
                        "ACCEPTED_MODEL_OBJECT_MISSING",
                        "document_state",
                        "The project records accepted build123d geometry, but its FreeCAD model object is missing.",
                        observed={
                            "model_id": model_id,
                            "accepted_revision": accepted_revision_before,
                            "artifact_directory": str(artifact["directory"]),
                        },
                    )
                )
            if target is not None:
                native_revision = str(getattr(target, PROP_REVISION, "") or "")
                if native_revision != accepted_revision_before:
                    raise Build123dFailure(
                        _failure(
                            "ACCEPTED_REVISION_DIVERGED",
                            "document_state",
                            "The accepted FreeCAD object and project artifact have different revisions.",
                            observed={
                                "freecad_revision": native_revision,
                                "artifact_accepted_revision": accepted_revision_before,
                            },
                        )
                    )
        else:
            current = _model_contract(target)
            base_revision = str(getattr(target, PROP_REVISION, "") or "")
            accepted_revision_before = base_revision
            accepted_outputs = json.loads(str(getattr(target, PROP_OUTPUTS) or "{}"))
            accepted_output_facts = {}
        expected_revision = str(arguments.get("expected_revision") or "").strip()
        if expected_revision != base_revision:
            raise Build123dFailure(
                _failure(
                    "STALE_MODEL_REVISION",
                    "precondition",
                    "The build123d model changed after it was inspected.",
                    requested={"expected_revision": expected_revision},
                    observed={"current_revision": base_revision},
                    required_changes=[{"inspect_model": model_id}],
                )
            )
        model_name = current["model_name"]
        source = current["source"]
        parameters = current["parameters"]
        input_objects = current["input_objects"]
        expected_outputs = current["expected_outputs"]
        if operation == "build123d.edit_source":
            source = _apply_source_edits(source, arguments.get("edits"))
        elif operation == "build123d.set_parameters":
            patch = _json_object(arguments.get("patch"), "patch")
            if not patch:
                raise Build123dFailure(
                    _failure("EMPTY_PARAMETER_PATCH", "schema", "patch cannot be empty.")
                )
            parameters = _merge_patch(parameters, patch)
            if not isinstance(parameters, dict):
                raise Build123dFailure(
                    _failure(
                        "INVALID_PARAMETER_RESULT",
                        "schema",
                        "The parameter merge patch must leave params as an object.",
                    )
                )
        elif operation == "build123d.set_inputs":
            input_objects = _clean_inputs(arguments.get("input_objects"))
        elif operation == "build123d.reconfigure_model":
            source = str(arguments.get("source") or "")
            parameters = _json_object(arguments.get("parameters"), "parameters")
            input_objects = _clean_inputs(arguments.get("input_objects"))
            expected_outputs = _clean_outputs(arguments.get("expected_outputs"))
        elif operation == "build123d.editor_rebuild":
            pass
        else:
            raise Build123dFailure(
                _failure(
                    "UNSUPPORTED_BUILD123D_TOOL",
                    "surface",
                    f"Unsupported runner-backed build123d tool: {operation}",
                )
            )
        validate_source(source)

    revision = source_revision(source, parameters, input_objects, expected_outputs)
    if (
        not creating
        and revision == base_revision
        and operation != "build123d.editor_rebuild"
    ):
        raise Build123dFailure(
            _failure(
                "NO_MODEL_CHANGE",
                "precondition",
                "The requested build123d edit produces the existing model revision.",
                observed={"revision": revision},
                required_changes=[{"change_source_parameters_or_inputs": True}],
            )
        )

    staging = project_root / "build123d" / ".staging" / uuid.uuid4().hex
    inputs_directory = staging / "inputs"
    outputs_directory = staging / "outputs"
    inputs_directory.mkdir(parents=True, exist_ok=False)
    outputs_directory.mkdir(parents=True, exist_ok=False)

    input_files: dict[str, str] = {}
    try:
        for index, (alias, object_name) in enumerate(input_objects.items()):
            obj = doc.getObject(object_name)
            shape = getattr(obj, "Shape", None) if obj is not None else None
            if obj is None or shape is None or bool(shape.isNull()):
                raise Build123dFailure(
                    _failure(
                        "INVALID_INPUT_OBJECT",
                        "input_export",
                        f"Input {alias!r} does not reference a shaped FreeCAD object.",
                        observed={"alias": alias, "object_name": object_name},
                    )
                )
            relative = Path("inputs") / f"{index:03d}.step"
            shape.exportStep(str(staging / relative))
            input_files[alias] = str(relative)

        configured_timeout, configured_memory = _configured_budgets()
        request = {
            "schema": "vibecad-build123d-execution-v1",
            "build123d_version": BUILD123D_VERSION,
            "source": source,
            "parameters": parameters,
            "inputs": input_files,
            "expected_outputs": expected_outputs,
            "output_directory": "outputs",
            "memory_limit_bytes": configured_memory,
            "cpu_limit_seconds": max(DEFAULT_CPU_LIMIT_SECONDS, int(configured_timeout)),
            "output_limit_bytes": DEFAULT_OUTPUT_LIMIT_BYTES,
        }
        _write_json(staging / "request.json", request)
        shutil.copy2(Path(__file__).resolve().parent / "build123d_worker.py", staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    prepared = {
        "model_id": model_id,
        "creating": creating,
        "operation": operation,
        "model_name": model_name,
        "source": source,
        "parameters": parameters,
        "input_objects": input_objects,
        "expected_outputs": expected_outputs,
        "revision": revision,
        "base_revision": base_revision,
        "accepted_revision_before": accepted_revision_before,
        "accepted_outputs": accepted_outputs,
        "accepted_output_facts": accepted_output_facts,
        "project_root": str(project_root),
        "staging": str(staging),
        "health": health,
        "document_name": doc.Name,
        "cad_revision_before": service.structural_document_revision(),
    }
    try:
        prepared["artifacts"] = _persist_working_candidate(prepared)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return prepared


def _runner_command(prepared: dict[str, Any]) -> list[str]:
    health = prepared["health"]
    executable = Path(str(health["python_executable"]))
    runtime = Path(str(health["runtime_root"]))
    staging = Path(str(prepared["staging"]))
    return [
        str(executable),
        "-I",
        "-S",
        str(staging / "build123d_worker.py"),
        str(staging / "request.json"),
        str(staging / "result.json"),
        str(runtime / "site-packages"),
    ]


def _runner_environment(staging: Path) -> dict[str, str]:
    preserved = (
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "WINDIR",
    )
    environment = {
        name: os.environ[name]
        for name in preserved
        if str(os.environ.get(name) or "").strip()
    }
    environment.update(
        {
            "HOME": str(staging),
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "TEMP": str(staging),
            "TMP": str(staging),
            "TMPDIR": str(staging),
        }
    )
    if sys.platform == "win32":
        # pathlib.Path.home() ignores HOME on Windows.  Keep build123d and
        # IPython inside the isolated staging directory without exposing the
        # user's real profile to the worker process.
        home_drive, home_path = os.path.splitdrive(str(staging))
        environment["USERPROFILE"] = str(staging)
        if home_drive:
            environment["HOMEDRIVE"] = home_drive
            environment["HOMEPATH"] = home_path or "\\"
    return environment


def _configured_budgets() -> tuple[float, int]:
    """Preference-driven (timeout_seconds, memory_limit_bytes) with safe defaults."""
    timeout = DEFAULT_TIMEOUT_SECONDS
    memory = DEFAULT_MEMORY_LIMIT_BYTES
    try:
        settings = load_settings()
        configured_timeout = float(
            getattr(settings, "scripted_timeout_seconds", 0.0) or 0.0
        )
        configured_memory_mb = int(
            getattr(settings, "scripted_memory_limit_mb", 0) or 0
        )
    except Exception:
        return timeout, memory
    if configured_timeout > 0:
        timeout = configured_timeout
    if configured_memory_mb > 0:
        memory = configured_memory_mb * 1024 * 1024
    return timeout, memory


def _resolved_budgets(
    timeout_seconds: float | None, memory_limit_bytes: int | None
) -> tuple[float, int]:
    """Resolve explicit budget overrides against preference-driven values."""
    if timeout_seconds is not None and memory_limit_bytes is not None:
        return float(timeout_seconds), int(memory_limit_bytes)
    configured_timeout, configured_memory = _configured_budgets()
    return (
        float(timeout_seconds) if timeout_seconds is not None else configured_timeout,
        int(memory_limit_bytes)
        if memory_limit_bytes is not None
        else configured_memory,
    )


def _process_memory_bytes(pid: int) -> int | None:
    """Best-effort peak resident memory of ``pid`` in bytes; None when unknown."""
    if sys.platform == "win32":
        return _windows_process_memory_bytes(pid)
    if sys.platform == "darwin":
        return _darwin_process_memory_bytes(pid)
    try:
        status = Path(f"/proc/{pid}/status").read_text(
            encoding="ascii", errors="replace"
        )
    except OSError:
        return None
    fallback: int | None = None
    for line in status.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if line.startswith("VmHWM:"):
            try:
                return int(parts[1]) * 1024
            except ValueError:
                return None
        if line.startswith("VmRSS:"):
            try:
                fallback = int(parts[1]) * 1024
            except ValueError:
                fallback = None
    return fallback


def _darwin_process_memory_bytes(pid: int) -> int | None:
    """Resident memory bytes for ``pid`` from the native macOS process table."""
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(int(pid))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="ascii",
            errors="replace",
            timeout=1.0,
        )
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return int(value) * 1024 if value else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _windows_process_memory_bytes(pid: int) -> int | None:
    """Peak working-set bytes for ``pid`` via psapi; None when unavailable."""
    import ctypes
    from ctypes import wintypes

    class _MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    process_query_limited_information = 0x1000
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
    except AttributeError:
        return None
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return None
    try:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def execute_prepared(
    prepared: dict[str, Any],
    *,
    cancellation_check: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
    memory_limit_bytes: int | None = None,
) -> dict[str, Any]:
    timeout_seconds, memory_limit_bytes = _resolved_budgets(
        timeout_seconds, memory_limit_bytes
    )
    command = _runner_command(prepared)
    staging = Path(str(prepared["staging"]))
    started = time.monotonic()
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        process = subprocess.Popen(
            command,
            cwd=str(staging),
            env=_runner_environment(staging),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=sys.platform != "win32",
            creationflags=creation_flags,
        )
    except Exception as exc:
        return _failure(
            "RUNNER_START_FAILED",
            "runtime",
            f"build123d runner could not start: {exc}",
            observed={"python_executable": command[0]},
        )
    cancelled = False
    timed_out = False
    memory_exceeded = False
    observed_memory: int | None = None
    next_memory_check = 0.0
    while process.poll() is None:
        if cancellation_check is not None and cancellation_check():
            cancelled = True
            break
        now = time.monotonic()
        if now - started > timeout_seconds:
            timed_out = True
            break
        if memory_limit_bytes > 0 and now >= next_memory_check:
            next_memory_check = now + 0.5
            usage = _process_memory_bytes(process.pid)
            if usage is not None:
                observed_memory = usage
                if usage > memory_limit_bytes:
                    memory_exceeded = True
                    break
        time.sleep(0.1)
    if cancelled or timed_out or memory_exceeded:
        try:
            if sys.platform != "win32":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=3.0)
        except Exception:
            process.kill()
            process.wait(timeout=3.0)
    stdout, stderr = process.communicate()
    elapsed = time.monotonic() - started
    if cancelled:
        return _failure(
            "RUN_CANCELLED",
            "execution",
            "build123d execution was cancelled.",
            observed={"elapsed_seconds": elapsed},
            cancelled=True,
        )
    if memory_exceeded:
        return _failure(
            "MEMORY_LIMIT_EXCEEDED",
            "execution",
            "build123d execution exceeded the "
            f"{memory_limit_bytes // (1024 * 1024)} MB memory budget.",
            observed={
                "memory_limit_bytes": memory_limit_bytes,
                "observed_memory_bytes": observed_memory,
                "elapsed_seconds": elapsed,
            },
            required_changes=[
                {"reduce_model_memory_or_increase_memory_budget_preference": True}
            ],
        )
    if timed_out:
        return _failure(
            "EXECUTION_TIMEOUT",
            "execution",
            f"build123d execution exceeded {timeout_seconds:.0f} seconds.",
            observed={"elapsed_seconds": elapsed},
        )
    result_path = staging / "result.json"
    if not result_path.is_file():
        return _failure(
            "RUNNER_NO_RESULT",
            "execution",
            "build123d runner exited without a result.",
            observed={
                "exit_code": process.returncode,
                "stdout": stdout[-8000:],
                "stderr": stderr[-8000:],
                "elapsed_seconds": elapsed,
            },
        )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _failure(
            "RUNNER_INVALID_RESULT",
            "execution",
            f"build123d runner result is invalid: {exc}",
            observed={"exit_code": process.returncode, "stderr": stderr[-8000:]},
        )
    if not isinstance(result, dict) or not result.get("ok"):
        exception_kind = (
            str(result.get("exception_kind") or "")
            if isinstance(result, dict)
            else ""
        )
        evidence = (
            result.get("exception_evidence")
            if isinstance(result, dict)
            and isinstance(result.get("exception_evidence"), dict)
            else {}
        )
        failure_code = {
            "design_assertion_failure": "BUILD123D_DESIGN_ASSERTION_FAILED",
            "kernel_fillet_failure": "BUILD123D_FILLET_FAILED",
            "shape_collection_contract_failure": "BUILD123D_SHAPE_COLLECTION_MISUSED",
        }.get(exception_kind, "BUILD123D_EXECUTION_FAILED")
        return _failure(
            failure_code,
            "execution",
            str(result.get("error") if isinstance(result, dict) else "Runner failed."),
            observed={
                "exit_code": process.returncode,
                "exception_type": result.get("exception_type") if isinstance(result, dict) else None,
                "exception_kind": exception_kind or None,
                "traceback": result.get("traceback") if isinstance(result, dict) else None,
                "exception_evidence": evidence or None,
                "stdout": stdout[-8000:],
                "stderr": stderr[-8000:],
                "elapsed_seconds": elapsed,
            },
            required_changes=_execution_required_changes(exception_kind, evidence),
        )
    result["elapsed_seconds"] = elapsed
    result["stdout"] = stdout[-8000:]
    result["stderr"] = stderr[-8000:]
    return result


def _execution_required_changes(
    exception_kind: str,
    evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    if exception_kind != "kernel_fillet_failure":
        return [{"correct_source_parameters_or_inputs_from_traceback": True}]

    diagnostics = evidence.get("fillet_diagnostics")
    if not isinstance(diagnostics, dict):
        return [{"repair_fillet_source_from_traceback": True}]

    required: list[dict[str, Any]] = [{"do_not_retry_unchanged_fillet": True}]
    components = [
        item
        for item in diagnostics.get("connected_components") or []
        if isinstance(item, dict)
    ]
    open_components = [
        int(item.get("component_index"))
        for item in components
        if not bool(item.get("closed_loop"))
    ]
    if open_components:
        required.append(
            {
                "repair_open_selected_edge_components": open_components,
                "selection_indices": [
                    list(item.get("selection_indices") or [])
                    for item in components
                    if int(item.get("component_index")) in open_components
                ],
            }
        )
    if diagnostics.get("separate_component_fillet_possible"):
        required.append(
            {
                "apply_fillet_per_connected_component": [
                    list(item.get("selection_indices") or []) for item in components
                ]
            }
        )

    reduced_radius: dict[int, float] = {}
    for trial in diagnostics.get("component_trials") or []:
        if not isinstance(trial, dict) or not trial.get("succeeded"):
            continue
        factor = trial.get("radius_factor")
        if factor is None:
            continue
        component = int(trial.get("component_index") or 0)
        radius = float(trial.get("radius_mm") or 0.0)
        reduced_radius[component] = max(reduced_radius.get(component, 0.0), radius)
    if reduced_radius:
        required.append(
            {
                "maximum_tested_working_radius_by_component_mm": {
                    str(component): radius
                    for component, radius in sorted(reduced_radius.items())
                }
            }
        )

    failed_edges = [
        int(trial.get("selection_index"))
        for trial in diagnostics.get("individual_edge_trials") or []
        if isinstance(trial, dict) and not trial.get("succeeded")
    ]
    if failed_edges:
        required.append({"repair_or_exclude_failing_selection_indices": failed_edges})
    if not diagnostics.get("diagnostic_complete", True):
        required.append(
            {
                "diagnostic_incomplete": str(
                    diagnostics.get("diagnostic_stop_reason") or "time_budget_exhausted"
                )
            }
        )
    if len(required) == 1:
        required.append(
            {
                "rebuild_transition_geometry_or_reduce_radius": True,
                "prefer_modeled_transition_over_kernel_dressup_when_clearance_is_tight": True,
            }
        )
    return required


def _freecad_shape_facts(shape: Any) -> dict[str, Any]:
    if shape is None or bool(shape.isNull()):
        return {
            "valid": False,
            "is_null": True,
            "solid_count": 0,
            "face_count": 0,
            "edge_count": 0,
            "vertex_count": 0,
            "volume_mm3": 0.0,
            "surface_area_mm2": 0.0,
            "state": "empty_shape",
        }
    bounds = shape.BoundBox
    solids = list(shape.Solids)
    faces = list(shape.Faces)
    edges = list(shape.Edges)
    center = solids[0].CenterOfMass if len(solids) == 1 else bounds.Center
    facts = {
        "valid": bool(shape.isValid()),
        "is_null": False,
        "solid_count": len(solids),
        "face_count": len(faces),
        "edge_count": len(edges),
        "vertex_count": len(list(shape.Vertexes)),
        "volume_mm3": float(shape.Volume),
        "surface_area_mm2": float(shape.Area),
        "center": [
            float(center.x),
            float(center.y),
            float(center.z),
        ],
        "bounds_mm": {
            "min": [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
            "max": [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
            "size": [float(bounds.XLength), float(bounds.YLength), float(bounds.ZLength)],
            "diagonal": float(bounds.DiagonalLength),
        },
    }
    if edges:
        facts["minimum_edge_length_mm"] = min(float(edge.Length) for edge in edges)
    if faces:
        facts["minimum_face_area_mm2"] = min(float(face.Area) for face in faces)
    for label, items, attribute in (
        ("face_geometry_types", faces, "Surface"),
        ("edge_geometry_types", edges, "Curve"),
    ):
        histogram: dict[str, int] = {}
        for item in items:
            try:
                geometry_type = type(getattr(item, attribute)).__name__.lower()
            except Exception:
                geometry_type = "unknown"
            histogram[geometry_type] = histogram.get(geometry_type, 0) + 1
        facts[label] = histogram
    return facts


def _compare_shape_facts(
    key: str,
    worker: dict[str, Any],
    native: dict[str, Any],
) -> dict[str, Any]:
    if (
        not worker.get("valid")
        or int(worker.get("solid_count") or 0) != 1
        or not native["valid"]
        or native["solid_count"] != 1
    ):
        raise Build123dFailure(
            _failure(
                "STEP_IMPORT_INVALID",
                "step_import",
                f"Imported output {key!r} is not one valid FreeCAD solid.",
                observed={"build123d": worker, "freecad": native},
            )
        )
    worker_volume = float(worker.get("volume_mm3") or 0.0)
    native_volume = float(native.get("volume_mm3") or 0.0)
    volume_delta = abs(worker_volume - native_volume)
    relative_volume_tolerance = 5.0e-5
    volume_tolerance = max(1.0e-3, abs(worker_volume) * relative_volume_tolerance)
    relative_volume_delta = (
        volume_delta / abs(worker_volume) if abs(worker_volume) > 1.0e-12 else None
    )
    worker_bounds = worker.get("bounds_mm") or {}
    native_bounds = native.get("bounds_mm") or {}
    diagonal = max(
        float(worker_bounds.get("diagonal") or 0.0),
        float(native_bounds.get("diagonal") or 0.0),
        1.0,
    )
    linear_tolerance = max(1.0e-5, diagonal * 1.0e-6)
    transfer = {
        "volume_delta_mm3": volume_delta,
        "relative_volume_delta": relative_volume_delta,
        "relative_volume_tolerance": relative_volume_tolerance,
        "volume_tolerance_mm3": volume_tolerance,
        "linear_tolerance_mm": linear_tolerance,
        "bounds_delta_mm": {},
    }
    if volume_delta > volume_tolerance:
        raise Build123dFailure(
            _failure(
                "STEP_VOLUME_MISMATCH",
                "step_import",
                f"STEP transfer changed output {key!r} volume beyond tolerance.",
                observed={
                    "build123d_volume_mm3": worker_volume,
                    "freecad_volume_mm3": native_volume,
                    "volume_delta_mm3": volume_delta,
                    "relative_volume_delta": relative_volume_delta,
                    "tolerance_mm3": volume_tolerance,
                    "build123d": worker,
                    "freecad": native,
                },
            )
        )
    for bound in ("min", "max", "size"):
        expected = list(worker_bounds.get(bound) or [])
        actual = list(native_bounds.get(bound) or [])
        if len(expected) != 3 or len(actual) != 3:
            raise Build123dFailure(
                _failure(
                    "STEP_BOUNDS_MISSING",
                    "step_import",
                    f"STEP transfer did not return complete {bound} bounds for {key!r}.",
                )
            )
        deltas = [abs(float(a) - float(b)) for a, b in zip(expected, actual)]
        transfer["bounds_delta_mm"][bound] = deltas
        if any(delta > linear_tolerance for delta in deltas):
            raise Build123dFailure(
                _failure(
                    "STEP_BOUNDS_MISMATCH",
                    "step_import",
                    f"STEP transfer changed output {key!r} bounds beyond tolerance.",
                    observed={
                        "bound": bound,
                        "build123d": expected,
                        "freecad": actual,
                        "delta_mm": deltas,
                        "tolerance_mm": linear_tolerance,
                    },
                )
            )
    transfer["maximum_bounds_delta_mm"] = max(
        (
            delta
            for values in transfer["bounds_delta_mm"].values()
            for delta in values
        ),
        default=0.0,
    )
    return transfer


def import_validated_outputs(prepared: dict[str, Any], execution: dict[str, Any]) -> list[dict[str, Any]]:
    import Part

    staging = Path(str(prepared["staging"])).resolve()
    raw_outputs = execution.get("outputs")
    if not isinstance(raw_outputs, list):
        raise Build123dFailure(
            _failure("OUTPUT_MANIFEST_MISSING", "step_import", "Runner returned no output list.")
        )
    if [str(item.get("key") or "") for item in raw_outputs] != prepared["expected_outputs"]:
        raise Build123dFailure(
            _failure(
                "OUTPUT_MANIFEST_MISMATCH",
                "step_import",
                "Runner output keys do not match the prepared request.",
                observed={"outputs": raw_outputs},
            )
        )
    imported: list[dict[str, Any]] = []
    for item in raw_outputs:
        key = str(item["key"])
        path = (staging / str(item.get("step_path") or "")).resolve()
        if staging not in path.parents or not path.is_file():
            raise Build123dFailure(
                _failure(
                    "OUTPUT_FILE_INVALID",
                    "step_import",
                    f"Runner output file for {key!r} is missing or outside staging.",
                )
            )
        try:
            shape = Part.read(str(path))
        except Exception as exc:
            raise Build123dFailure(
                _failure(
                    "STEP_IMPORT_FAILED",
                    "step_import",
                    f"FreeCAD could not import output {key!r}: {exc}",
                )
            ) from exc
        native_facts = _freecad_shape_facts(shape)
        worker_facts = dict(item.get("shape") or {})
        transfer = _compare_shape_facts(key, worker_facts, native_facts)
        imported.append(
            {
                "key": key,
                "shape": shape,
                "build123d_shape": worker_facts,
                "freecad_shape": native_facts,
                "step_transfer": transfer,
            }
        )
    return imported


def runtime_execution_smoke() -> dict[str, Any]:
    health = runtime_health(refresh=True)
    if not health.get("ready"):
        raise RuntimeError(str(health.get("error") or "build123d runtime is unavailable."))
    with tempfile.TemporaryDirectory(prefix="vibecad-build123d-smoke-") as temporary:
        staging = Path(temporary)
        (staging / "outputs").mkdir()
        request = {
            "schema": "vibecad-build123d-execution-v1",
            "build123d_version": BUILD123D_VERSION,
            "source": (
                "from build123d import *\n"
                "result = {'Runtime Smoke': Box(2, 3, 5)}\n"
            ),
            "parameters": {},
            "inputs": {},
            "expected_outputs": ["Runtime Smoke"],
            "output_directory": "outputs",
            "memory_limit_bytes": DEFAULT_MEMORY_LIMIT_BYTES,
            "cpu_limit_seconds": 60,
            "output_limit_bytes": 64 * 1024 * 1024,
        }
        _write_json(staging / "request.json", request)
        shutil.copy2(Path(__file__).resolve().parent / "build123d_worker.py", staging)
        prepared = {
            "health": health,
            "staging": str(staging),
            "expected_outputs": ["Runtime Smoke"],
        }
        execution = execute_prepared(prepared, timeout_seconds=90.0)
        if not execution.get("ok"):
            raise RuntimeError(_canonical_json(execution))
        imported = import_validated_outputs(prepared, execution)
        facts = imported[0]["freecad_shape"]
        if abs(float(facts["volume_mm3"]) - 30.0) > 1.0e-7:
            raise RuntimeError(f"Unexpected build123d smoke volume: {facts}")
        return {
            "ok": True,
            "version": execution.get("build123d_version"),
            "shape": facts,
        }


def _add_string_property(obj: Any, name: str, group: str = "Build123d") -> None:
    if name not in list(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty("App::PropertyString", name, group)


def _safe_internal_name(value: str, prefix: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))
    clean = re.sub(r"_+", "_", clean).strip("_")
    if not clean or not clean[0].isalpha():
        clean = f"{prefix}_{clean}" if clean else prefix
    return clean[:80]


def _output_objects(container: Any) -> dict[str, tuple[Any, Any]]:
    result: dict[str, tuple[Any, Any]] = {}
    for body in list(getattr(container, "Group", []) or []):
        if getattr(body, "TypeId", "") != "PartDesign::Body":
            continue
        key = str(getattr(body, PROP_OUTPUT_KEY, "") or "")
        if not key:
            continue
        features = list(getattr(body, "Group", []) or [])
        feature = next(
            (
                item
                for item in features
                if str(getattr(item, PROP_OUTPUT_KEY, "") or "") == key
            ),
            None,
        )
        if feature is not None:
            result[key] = (body, feature)
    return result


def _set_shaded_display(obj: Any) -> None:
    view = getattr(obj, "ViewObject", None)
    if view is None:
        # Headless sessions (FreeCADCmd) have no view providers; the display
        # contract only applies when a GUI is attached.
        return
    modes = list(view.listDisplayModes())
    if "Shaded" not in modes:
        raise RuntimeError(
            f"build123d output {obj.Name} cannot use Shaded display mode. "
            f"Available modes: {modes}"
        )
    if str(view.DisplayMode) != "Shaded":
        view.DisplayMode = "Shaded"


def restore_output_display_modes(doc: Any) -> list[str]:
    """Restore the edge-free display contract for accepted build123d outputs."""
    restored: list[str] = []
    for container in _model_objects(doc):
        for body, feature in _output_objects(container).values():
            for obj in (body, feature):
                if getattr(obj, "ViewObject", None) is None:
                    continue
                _set_shaded_display(obj)
                restored.append(str(obj.Name))
    return restored


def _mirror_model(
    project_root: str,
    container: Any,
    committed: list[dict[str, Any]],
) -> dict[str, str]:
    model_id = str(getattr(container, PROP_MODEL_ID))
    directory = Path(project_root) / "build123d" / model_id
    directory.mkdir(parents=True, exist_ok=True)
    source_path = directory / "model.py"
    parameters_path = directory / "parameters.json"
    manifest_path = directory / "manifest.json"
    revisions_directory = directory / "revisions"
    revisions_directory.mkdir(parents=True, exist_ok=True)
    revision = str(getattr(container, PROP_REVISION))
    revision_source_path = revisions_directory / f"{revision}.py"
    revision_parameters_path = revisions_directory / f"{revision}.parameters.json"
    revision_manifest_path = revisions_directory / f"{revision}.manifest.json"
    source = str(getattr(container, PROP_SOURCE))
    parameters = json.loads(str(getattr(container, PROP_PARAMETERS) or "{}"))
    inputs = json.loads(str(getattr(container, PROP_INPUTS) or "{}"))
    outputs = json.loads(str(getattr(container, PROP_OUTPUTS) or "{}"))
    output_facts = {
        item["key"]: {
            "build123d_shape": item["build123d_shape"],
            "step_transfer": item["step_transfer"],
        }
        for item in committed
    }
    if revision_source_path.exists() and revision_source_path.read_text(encoding="utf-8") != source:
        raise RuntimeError(
            f"build123d revision {revision} already exists with different source."
        )
    if not revision_source_path.exists():
        revision_source_path.write_text(source, encoding="utf-8")
        _write_json(revision_parameters_path, parameters)
        _write_json(
            revision_manifest_path,
            {
                "schema": MODEL_SCHEMA,
                "model_id": model_id,
                "label": str(container.Label),
                "revision": revision,
                "state": "accepted",
                "runtime_version": str(getattr(container, PROP_RUNTIME_VERSION)),
                "inputs": inputs,
                "expected_outputs": list(outputs),
                "outputs": outputs,
                "output_facts": output_facts,
            },
        )
    source_temp = source_path.with_name("model.py.tmp")
    source_temp.write_text(source, encoding="utf-8")
    source_temp.replace(source_path)
    _write_json(parameters_path, parameters)
    _write_json(
        manifest_path,
        {
            "schema": MODEL_SCHEMA,
            "model_id": model_id,
            "label": str(container.Label),
            "state": "accepted",
            "revision": revision,
            "working_revision": revision,
            "accepted_revision": revision,
            "runtime_version": str(getattr(container, PROP_RUNTIME_VERSION)),
            "inputs": inputs,
            "expected_outputs": list(outputs),
            "outputs": outputs,
            "output_facts": output_facts,
            "latest_attempt": {
                "revision": revision,
                "status": "accepted",
                "path": str(Path("attempts") / revision),
            },
        },
    )
    attempt_manifest_path = directory / "attempts" / revision / "manifest.json"
    if attempt_manifest_path.is_file():
        attempt_manifest = _read_json_object(attempt_manifest_path, "attempt manifest")
        attempt_manifest["status"] = "accepted"
        _write_json(attempt_manifest_path, attempt_manifest)
    return {
        "source": str(source_path),
        "parameters": str(parameters_path),
        "manifest": str(manifest_path),
        "revision_source": str(revision_source_path),
    }


def _recompute_errors(summary: Any) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        raise RuntimeError("FreeCAD returned an invalid recompute diagnostic payload.")
    if not bool(summary.get("captured")):
        raise RuntimeError(
            "FreeCAD recompute diagnostics are unavailable: "
            + str(summary.get("reason") or "no diagnostic reason was supplied")
        )
    diagnostics = summary.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise RuntimeError("FreeCAD recompute diagnostics did not contain a list.")
    return [
        dict(item)
        for item in diagnostics
        if isinstance(item, dict)
        and str(item.get("severity") or "").lower() == "error"
    ]


def commit_outputs(
    service: Any,
    prepared: dict[str, Any],
    execution: dict[str, Any],
    imported: list[dict[str, Any]],
) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None or doc.Name != prepared["document_name"]:
        raise Build123dFailure(
            _failure(
                "DOCUMENT_CHANGED",
                "commit",
                "The active document changed while build123d was running.",
                observed={
                    "expected_document": prepared["document_name"],
                    "active_document": getattr(doc, "Name", None),
                },
            )
        )
    if bool(getattr(doc, "Recomputing", False)):
        raise Build123dFailure(
            _failure(
                "DOCUMENT_RECOMPUTE_IN_PROGRESS",
                "commit",
                f"Document {doc.Name} is still recomputing; build123d outputs were not committed.",
                observed={"document": doc.Name, "recomputing": True},
            )
        )
    target = _find_model(doc, prepared["model_id"])
    accepted_revision_before = str(prepared.get("accepted_revision_before") or "")
    if not accepted_revision_before and target is not None:
        raise Build123dFailure(
            _failure(
                "MODEL_ID_COLLISION",
                "commit",
                "A build123d model with the generated id appeared while execution ran.",
            )
        )
    if accepted_revision_before:
        if target is None:
            raise Build123dFailure(
                _failure("MODEL_REMOVED", "commit", "The target model was removed while execution ran.")
            )
        current_revision = str(getattr(target, PROP_REVISION, "") or "")
        if current_revision != accepted_revision_before:
            raise Build123dFailure(
                _failure(
                    "MODEL_CHANGED_DURING_EXECUTION",
                    "commit",
                    "The build123d model changed while the sidecar was running; "
                    "the generated outputs were not committed.",
                    requested={"accepted_revision": accepted_revision_before},
                    observed={"current_revision": current_revision},
                    required_changes=[{"inspect_model": prepared["model_id"]}],
                )
            )

    created_container = target is None
    doc.openTransaction("Accept build123d model")
    try:
        container = target or doc.addObject(
            "App::Part", _safe_internal_name(prepared["model_name"], "Build123dModel")
        )
        for prop in (
            PROP_MODEL_ID,
            PROP_SOURCE,
            PROP_PARAMETERS,
            PROP_REVISION,
            PROP_RUNTIME_VERSION,
            PROP_OUTPUTS,
            PROP_INPUTS,
        ):
            _add_string_property(container, prop)

        existing = _output_objects(container)
        committed: list[dict[str, Any]] = []
        for item in imported:
            key = item["key"]
            pair = existing.get(key)
            if pair is None:
                body = doc.addObject(
                    "PartDesign::Body", _safe_internal_name(key, "Build123dBody")
                )
                body.Label = key
                container.addObject(body)
                _add_string_property(body, PROP_MODEL_ID)
                _add_string_property(body, PROP_OUTPUT_KEY)
                feature = body.newObject("PartDesign::Feature", "Build123dFeature")
                feature.Label = f"{key} (build123d)"
                _add_string_property(feature, PROP_MODEL_ID)
                _add_string_property(feature, PROP_OUTPUT_KEY)
            else:
                body, feature = pair
                body.Label = key
                feature.Label = f"{key} (build123d)"
            setattr(body, PROP_MODEL_ID, prepared["model_id"])
            setattr(body, PROP_OUTPUT_KEY, key)
            setattr(feature, PROP_MODEL_ID, prepared["model_id"])
            setattr(feature, PROP_OUTPUT_KEY, key)
            feature.Shape = item["shape"]
            body.Tip = feature
            _set_shaded_display(body)
            _set_shaded_display(feature)
            committed.append(
                {
                    "key": key,
                    "body": body.Name,
                    "feature": feature.Name,
                    "shape": item["freecad_shape"],
                    "build123d_shape": item["build123d_shape"],
                    "step_transfer": item["step_transfer"],
                }
            )

        retained = set(prepared["expected_outputs"])
        removed: list[str] = []
        for key, (body, feature) in existing.items():
            if key in retained:
                continue
            removed.append(body.Name)
            delete_contained_objects(doc, [body, feature])

        container.Label = prepared["model_name"]
        setattr(container, PROP_MODEL_ID, prepared["model_id"])
        setattr(container, PROP_SOURCE, prepared["source"])
        setattr(container, PROP_PARAMETERS, _canonical_json(prepared["parameters"]))
        setattr(container, PROP_REVISION, prepared["revision"])
        setattr(container, PROP_RUNTIME_VERSION, BUILD123D_VERSION)
        setattr(container, PROP_INPUTS, _canonical_json(prepared["input_objects"]))
        setattr(
            container,
            PROP_OUTPUTS,
            json.dumps(
                {
                    item["key"]: {
                        "body": item["body"],
                        "feature": item["feature"],
                    }
                    for item in committed
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        )
        doc.recompute()
        diagnostics = service.recompute_diagnostics()
        errors = _recompute_errors(diagnostics)
        if errors:
            first = errors[0]
            raise Build123dFailure(
                _failure(
                    "BUILD123D_COMMIT_FAILED",
                    "commit",
                    "FreeCAD reported errors while accepting build123d outputs. "
                    f"First: {first.get('code') or 'UNKNOWN'} on "
                    f"{first.get('object') or 'unknown object'}: "
                    f"{first.get('message') or 'no message'}",
                    observed={"recompute_errors": errors},
                )
            )
        mirror = _mirror_model(prepared["project_root"], container, committed)
        doc.commitTransaction()
    except Exception as exc:
        doc.abortTransaction()
        if isinstance(exc, Build123dFailure):
            raise
        raise Build123dFailure(
            _failure(
                "BUILD123D_COMMIT_FAILED",
                "commit",
                f"build123d geometry could not be committed: {exc}",
            )
        ) from exc
    return {
        "ok": True,
        "created": created_container,
        "updated": not created_container,
        "model": _model_summary(container, include_source=False),
        "outputs": committed,
        "removed_outputs": removed,
        "mirror": mirror,
        "execution": {
            "elapsed_seconds": execution.get("elapsed_seconds"),
            "build123d_version": execution.get("build123d_version"),
        },
        "native_diagnostics": diagnostics,
        "cad_revision": service.structural_document_revision(),
    }


def cleanup_prepared(prepared: dict[str, Any]) -> None:
    staging = Path(str(prepared.get("staging") or ""))
    if staging.name and ".staging" in staging.parts:
        shutil.rmtree(staging, ignore_errors=True)
