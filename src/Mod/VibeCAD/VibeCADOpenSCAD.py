# SPDX-License-Identifier: LGPL-2.1-or-later

"""Persisted, isolated OpenSCAD modeling engine for VibeCAD.

OpenSCAD source is the model authority.  The GUI process only prepares source,
imports validated BREP output, and commits accepted revisions.  OpenSCAD and
the CSG-to-OCC bridge always run in child processes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
import uuid

from VibeCADPreferences import load_settings
from VibeCADScriptedOwnership import (
    delete_contained_objects,
    delete_owned_model_objects,
    owned_model_objects,
)
from VibeCADTools import tool_failure


MODEL_SCHEMA = "vibecad-openscad-model-v1"
ATTEMPT_SCHEMA = "vibecad-openscad-attempt-v1"
OPENSCAD_VERSION = "2021.01"
MAX_SOURCE_BYTES = 1_000_000
MAX_PROJECT_SOURCE_BYTES = 4_000_000
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MEMORY_LIMIT_BYTES = 6 * 1024 * 1024 * 1024

PROP_MODEL_ID = "VibeCADOpenSCADModelId"
PROP_SOURCE = "VibeCADOpenSCADSource"
PROP_PARAMETERS = "VibeCADOpenSCADParameters"
PROP_REVISION = "VibeCADOpenSCADRevision"
PROP_RUNTIME_VERSION = "VibeCADOpenSCADRuntimeVersion"
PROP_OUTPUTS = "VibeCADOpenSCADOutputs"
PROP_OUTPUT_KEY = "VibeCADOpenSCADOutputKey"
PROP_FIDELITY = "VibeCADGeometryFidelity"
PROP_CONVERSION_MODE = "VibeCADOpenSCADConversionMode"

_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_. -]{0,95}$")
_MODEL_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_PARAMETER_NAME_PATTERN = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_Fidelity = frozenset({"exact_brep", "faceted_brep", "mixed"})
_CONVERSION_MODES = frozenset({"exact_brep", "faceted_brep"})
_runtime_cache: dict[str, dict[str, Any]] = {}


class OpenSCADFailure(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(str(payload.get("error") or "OpenSCAD operation failed."))


def _failure(
    code: str,
    stage: str,
    error: str,
    *,
    requested: Any = None,
    observed: Any = None,
    retry_same_call: bool = False,
    required_changes: list[Any] | None = None,
    **details: Any,
) -> dict[str, Any]:
    stage_map = {
        "schema": "schema",
        "surface": "surface",
        "precondition": "precondition",
        "runtime": "external_process",
        "execution": "external_process",
        "conversion": "external_process",
        "import": "native_call",
        "commit": "postcondition",
        "document_state": "precondition",
    }
    return tool_failure(
        "openscad",
        code,
        stage_map.get(stage, "external_process"),
        error,
        requested=requested,
        observed=observed,
        retry_same_call=retry_same_call,
        required_changes=required_changes,
        engine_stage=stage,
        **details,
    )


def _module_root() -> Path:
    return Path(__file__).resolve().parent


def bundled_runtime_root() -> Path:
    return _module_root() / "openscad_runtime"


def _bundled_runtime_version() -> str:
    manifest_path = bundled_runtime_root() / "runtime.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"OpenSCAD runtime manifest could not be read: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"OpenSCAD runtime manifest is invalid: {manifest_path}")
    version = str(manifest.get("openscad") or "").strip()
    if not version:
        raise RuntimeError(
            f"OpenSCAD runtime manifest does not declare an openscad version: "
            f"{manifest_path}"
        )
    return version


def _bundled_executable() -> Path:
    root = bundled_runtime_root()
    if sys.platform == "win32":
        return root / "openscad.exe"
    if sys.platform == "darwin":
        return root / "OpenSCAD.app" / "Contents" / "MacOS" / "OpenSCAD"
    return root / "bin" / "openscad"


def configured_executable(executable_override: str = "") -> tuple[Path, str]:
    clean_override = str(executable_override or "").strip()
    if not clean_override:
        clean_override = str(load_settings().openscad_executable or "").strip()
    if clean_override:
        return Path(clean_override).expanduser(), "preference"
    return _bundled_executable(), "bundled"


def _augment_runtime_environment(
    environment: dict[str, str], executable: Path
) -> dict[str, str]:
    result = dict(environment)
    runtime_root = bundled_runtime_root().resolve()
    try:
        executable.resolve().relative_to(runtime_root)
    except ValueError:
        return result
    if sys.platform.startswith("linux"):
        existing_library_path = str(result.get("LD_LIBRARY_PATH") or "").strip()
        result["LD_LIBRARY_PATH"] = os.pathsep.join(
            item
            for item in (str(runtime_root / "lib"), existing_library_path)
            if item
        )
        result["QT_PLUGIN_PATH"] = str(runtime_root / "plugins")
        result.setdefault("QT_QPA_PLATFORM", "offscreen")
    return result


def runtime_health(
    *, executable_override: str = "", refresh: bool = False
) -> dict[str, Any]:
    executable, source = configured_executable(executable_override)
    cache_key = str(executable)
    if not refresh and cache_key in _runtime_cache:
        return dict(_runtime_cache[cache_key])
    result: dict[str, Any] = {
        "ready": False,
        "version": OPENSCAD_VERSION,
        "runtime_version": None,
        "executable": str(executable),
        "source": source,
        "runtime_root": str(bundled_runtime_root()),
    }
    if not executable.is_file():
        result["error"] = (
            f"OpenSCAD executable is missing: {executable}. Install the bundled "
            "runtime or select an explicit executable in VibeCAD Preferences."
        )
        _runtime_cache[cache_key] = result
        return dict(result)
    try:
        expected_version = (
            _bundled_runtime_version() if source == "bundled" else OPENSCAD_VERSION
        )
        result["runtime_version"] = expected_version
        completed = subprocess.run(
            [str(executable), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=_augment_runtime_environment(os.environ, executable),
            creationflags=(
                int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if sys.platform == "win32"
                else 0
            ),
        )
        version_text = (completed.stdout or completed.stderr).strip()
        if completed.returncode != 0:
            raise RuntimeError(version_text or f"exit code {completed.returncode}")
        if expected_version not in version_text:
            raise RuntimeError(
                f"Expected OpenSCAD {expected_version}, received {version_text!r}."
            )
        result.update({"ready": True, "version": version_text})
    except Exception as exc:
        result["error"] = f"OpenSCAD runtime check failed: {exc}"
    _runtime_cache[cache_key] = result
    return dict(result)


def _freecadcmd_executable() -> Path:
    import FreeCAD as App

    bin_root = Path(str(App.getHomePath())) / "bin"
    names = (
        ("FreeCADCmd.exe", "freecadcmd.exe")
        if sys.platform == "win32"
        else ("FreeCADCmd", "freecadcmd")
    )
    for name in names:
        candidate = bin_root / name
        if candidate.is_file():
            return candidate
    raise OpenSCADFailure(
        _failure(
            "FREECADCMD_MISSING",
            "runtime",
            f"The isolated geometry converter is missing from {bin_root}.",
        )
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpenSCADFailure(
            _failure("INVALID_JSON_OBJECT", "schema", f"{label} must be an object.")
        )
    try:
        encoded = _canonical_json(value)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise OpenSCADFailure(
            _failure("INVALID_JSON_VALUE", "schema", f"{label} is not JSON-safe: {exc}")
        ) from exc
    return decoded


def _validate_parameter_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_parameter_value(item, f"{path}[{index}]")
        return
    raise OpenSCADFailure(
        _failure(
            "UNSUPPORTED_PARAMETER_VALUE",
            "schema",
            f"OpenSCAD parameter {path} must be a scalar or nested array, not {type(value).__name__}.",
        )
    )


def clean_parameters(value: Any) -> dict[str, Any]:
    parameters = _json_object(value, "parameters")
    for name, item in parameters.items():
        if not _PARAMETER_NAME_PATTERN.fullmatch(str(name)):
            raise OpenSCADFailure(
                _failure(
                    "INVALID_PARAMETER_NAME",
                    "schema",
                    f"OpenSCAD parameter name {name!r} is invalid.",
                )
            )
        _validate_parameter_value(item, str(name))
    return parameters


def clean_conversion_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in _CONVERSION_MODES:
        raise OpenSCADFailure(
            _failure(
                "INVALID_CONVERSION_MODE",
                "schema",
                "conversion_mode must be exact_brep or faceted_brep.",
                requested={"conversion_mode": value},
            )
        )
    return mode


def validate_source(source: str) -> None:
    if not isinstance(source, str) or not source.strip():
        raise OpenSCADFailure(
            _failure("EMPTY_SOURCE", "schema", "OpenSCAD source cannot be empty.")
        )
    encoded = source.encode("utf-8")
    if len(encoded) > MAX_SOURCE_BYTES:
        raise OpenSCADFailure(
            _failure(
                "SOURCE_TOO_LARGE",
                "schema",
                f"OpenSCAD source exceeds {MAX_SOURCE_BYTES:,} bytes.",
                observed={"source_bytes": len(encoded)},
            )
        )
    if "\x00" in source:
        raise OpenSCADFailure(
            _failure("SOURCE_CONTAINS_NUL", "schema", "OpenSCAD source contains a NUL byte.")
        )


def _clean_source_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".scad"
        or path.parts[0] in {"attempts", "revisions", ".staging"}
    ):
        raise OpenSCADFailure(
            _failure(
                "INVALID_SOURCE_PATH",
                "schema",
                f"OpenSCAD project source path {value!r} is unsafe or is not a .scad file.",
            )
        )
    return path.as_posix()


def clean_source_files(value: Any, main_source: str) -> dict[str, str]:
    validate_source(main_source)
    supplied = {} if value is None else _json_object(value, "source_files")
    files: dict[str, str] = {"model.scad": main_source}
    total_bytes = len(main_source.encode("utf-8"))
    for raw_path, raw_source in supplied.items():
        path = _clean_source_path(str(raw_path))
        source = str(raw_source)
        if path == "model.scad":
            if source != main_source:
                raise OpenSCADFailure(
                    _failure(
                        "MAIN_SOURCE_MISMATCH",
                        "schema",
                        "source_files['model.scad'] must exactly match source.",
                    )
                )
            continue
        if "\x00" in source:
            raise OpenSCADFailure(
                _failure(
                    "SOURCE_CONTAINS_NUL",
                    "schema",
                    f"OpenSCAD project source {path!r} contains a NUL byte.",
                )
            )
        encoded_size = len(source.encode("utf-8"))
        if encoded_size > MAX_SOURCE_BYTES:
            raise OpenSCADFailure(
                _failure(
                    "SOURCE_TOO_LARGE",
                    "schema",
                    f"OpenSCAD project source {path!r} exceeds {MAX_SOURCE_BYTES:,} bytes.",
                )
            )
        total_bytes += encoded_size
        files[path] = source
    if total_bytes > MAX_PROJECT_SOURCE_BYTES:
        raise OpenSCADFailure(
            _failure(
                "SOURCE_PROJECT_TOO_LARGE",
                "schema",
                f"OpenSCAD project sources exceed {MAX_PROJECT_SOURCE_BYTES:,} bytes.",
                observed={"source_bytes": total_bytes, "file_count": len(files)},
            )
        )
    return dict(sorted(files.items()))


def source_revision(
    source: str,
    parameters: dict[str, Any],
    source_files: dict[str, str] | None,
    conversion_mode: str,
) -> str:
    files = clean_source_files(source_files, source)
    mode = clean_conversion_mode(conversion_mode)
    digest = hashlib.sha256()
    for path, content in files.items():
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
        digest.update(b"\0")
    digest.update(_canonical_json(parameters).encode("utf-8"))
    digest.update(b"\0conversion_mode\0")
    digest.update(mode.encode("ascii"))
    return digest.hexdigest()


def _project_root(service: Any) -> Path:
    context = service.project_context()
    root = str(context.get("root") or "").strip()
    if not root:
        raise OpenSCADFailure(
            _failure(
                "PROJECT_NOT_PERSISTED",
                "precondition",
                "Save the active document before creating scripted models.",
            )
        )
    result = Path(root)
    result.mkdir(parents=True, exist_ok=True)
    return result


def _model_directory(project_root: str | Path, model_id: str) -> Path:
    return Path(project_root) / "openscad" / model_id


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OpenSCADFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"{label} could not be read from {path}: {exc}",
            )
        ) from exc
    if not isinstance(value, dict):
        raise OpenSCADFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"{label} at {path} is not an object.",
            )
        )
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _manifest_source_names(manifest: dict[str, Any]) -> list[str]:
    raw = manifest.get("source_files") or ["model.scad"]
    if not isinstance(raw, list):
        raise OpenSCADFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                "OpenSCAD manifest source_files must be an array.",
            )
        )
    names = {_clean_source_path(str(item)) for item in raw}
    names.add("model.scad")
    return sorted(names)


def _read_project_source_files(
    directory: Path,
    manifest: dict[str, Any],
    main_source: str,
) -> dict[str, str]:
    files = {"model.scad": main_source}
    for name in _manifest_source_names(manifest):
        if name == "model.scad":
            continue
        path = directory / name
        try:
            files[name] = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OpenSCADFailure(
                _failure(
                    "MODEL_ARTIFACT_INVALID",
                    "document_state",
                    f"OpenSCAD project source {name!r} could not be read: {exc}",
                )
            ) from exc
    return clean_source_files(files, main_source)


def _write_project_source_files(
    directory: Path,
    files: dict[str, str],
    previous_names: list[str] | None = None,
) -> None:
    normalized = clean_source_files(files, files.get("model.scad", ""))
    previous = {_clean_source_path(name) for name in (previous_names or [])}
    for name in sorted(previous - normalized.keys()):
        path = directory / name
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != directory:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    for name, source in normalized.items():
        _write_text(directory / name, source)


def _artifact_contract(project_root: str | Path, model_id: str) -> dict[str, Any] | None:
    if not _MODEL_ID_PATTERN.fullmatch(model_id):
        return None
    directory = _model_directory(project_root, model_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = _read_json(manifest_path, "OpenSCAD manifest")
    if manifest.get("schema") != MODEL_SCHEMA or manifest.get("model_id") != model_id:
        raise OpenSCADFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"OpenSCAD manifest identity is invalid: {manifest_path}",
            )
        )
    source_path = directory / "model.scad"
    parameters_path = directory / "parameters.json"
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpenSCADFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"OpenSCAD source could not be read: {exc}",
            )
        ) from exc
    parameters = clean_parameters(_read_json(parameters_path, "OpenSCAD parameters"))
    conversion_mode = clean_conversion_mode(manifest.get("conversion_mode"))
    working_revision = str(manifest.get("working_revision") or manifest.get("revision") or "")
    source_files = _read_project_source_files(directory, manifest, source)
    calculated = source_revision(source, parameters, source_files, conversion_mode)
    if working_revision != calculated:
        raise OpenSCADFailure(
            _failure(
                "MODEL_ARTIFACT_REVISION_MISMATCH",
                "document_state",
                "OpenSCAD source and metadata do not match the working revision.",
                observed={"manifest_revision": working_revision, "calculated_revision": calculated},
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
        "source_files": source_files,
        "parameters": parameters,
        "conversion_mode": conversion_mode,
        "working_revision": working_revision,
        "accepted_revision": accepted_revision,
        "state": state,
        "outputs": dict(manifest.get("outputs") or {}),
        "output_facts": dict(manifest.get("output_facts") or {}),
        "fidelity": str(manifest.get("fidelity") or ""),
        "latest_attempt": dict(manifest.get("latest_attempt") or {}),
        "directory": directory,
        "manifest": manifest,
    }


def _model_objects(doc: Any) -> list[Any]:
    return [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if PROP_MODEL_ID in list(getattr(obj, "PropertiesList", []) or [])
        and str(getattr(obj, PROP_MODEL_ID, "") or "")
        and str(getattr(obj, "TypeId", "")) == "App::Part"
    ]


def _find_model(doc: Any, model_id: str) -> Any | None:
    for obj in _model_objects(doc):
        if str(getattr(obj, PROP_MODEL_ID, "") or "") == model_id:
            return obj
    return None


def _model_summary(container: Any, *, include_source: bool) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    try:
        outputs = json.loads(str(getattr(container, PROP_OUTPUTS, "{}") or "{}"))
    except ValueError:
        outputs = {}
    summary = {
        "model_id": str(getattr(container, PROP_MODEL_ID, "") or ""),
        "object_name": str(container.Name),
        "label": str(container.Label),
        "working_revision": str(getattr(container, PROP_REVISION, "") or ""),
        "accepted_revision": str(getattr(container, PROP_REVISION, "") or ""),
        "state": "accepted",
        "parameters": json.loads(str(getattr(container, PROP_PARAMETERS, "{}") or "{}")),
        "conversion_mode": str(getattr(container, PROP_CONVERSION_MODE, "") or ""),
        "outputs": outputs,
        "fidelity": str(getattr(container, PROP_FIDELITY, "") or ""),
    }
    if include_source:
        summary["source"] = str(getattr(container, PROP_SOURCE, "") or "")
        summary["source_files"] = {"model.scad": summary["source"]}
    return summary


def _artifact_summary(contract: dict[str, Any], *, include_source: bool) -> dict[str, Any]:
    summary = {
        "model_id": contract["model_id"],
        "object_name": "",
        "label": contract["model_name"],
        "working_revision": contract["working_revision"],
        "accepted_revision": contract["accepted_revision"],
        "state": contract["state"],
        "parameters": contract["parameters"],
        "conversion_mode": contract["conversion_mode"],
        "outputs": contract["outputs"],
        "fidelity": contract["fidelity"],
    }
    if include_source:
        summary["source"] = contract["source"]
        summary["source_files"] = contract["source_files"]
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
    models_root = root / "openscad" if root else None
    if models_root is not None and models_root.is_dir():
        for directory in sorted(models_root.iterdir()):
            if not directory.is_dir() or not _MODEL_ID_PATTERN.fullmatch(directory.name):
                continue
            contract = _artifact_contract(root, directory.name)
            if contract is None:
                continue
            item = _artifact_summary(contract, include_source=False)
            native = summaries.get(directory.name)
            if native is not None:
                item["object_name"] = native.get("object_name", "")
            summaries[directory.name] = item
    return sorted(
        summaries.values(),
        key=lambda item: (str(item.get("label")), str(item.get("model_id"))),
    )


def model_summaries(doc: Any, project_root: str | Path | None = None) -> list[dict[str, Any]]:
    return complete_editor_model_index(editor_model_index_snapshot(doc), project_root)


def editor_model_inspection_snapshot(service: Any, model_id: str) -> dict[str, Any]:
    """Capture one model's native properties without inspecting its geometry."""

    doc = service._active_document()
    if doc is None:
        return _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
    try:
        project_root = _project_root(service)
        container = _find_model(doc, model_id)
    except OpenSCADFailure as exc:
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
    except OpenSCADFailure as exc:
        return exc.payload
    if not isinstance(native, dict) and contract is None:
        return _failure(
            "MODEL_NOT_FOUND",
            "precondition",
            f"No OpenSCAD model has id {model_id!r}.",
        )
    model = (
        _artifact_summary(contract, include_source=True)
        if contract is not None
        else dict(native)
    )
    if isinstance(native, dict):
        model["object_name"] = str(native.get("object_name") or "")
    if contract is not None:
        model["artifact_directory"] = str(contract["directory"])
        model["latest_attempt"] = contract["latest_attempt"]
        accepted = contract["accepted_revision"]
        if accepted and accepted != contract["working_revision"]:
            accepted_path = contract["directory"] / "revisions" / f"{accepted}.scad"
            if accepted_path.is_file():
                model["accepted_source"] = accepted_path.read_text(encoding="utf-8")
    return {
        "ok": True,
        "model": model,
    }


def _output_objects(container: Any) -> dict[str, tuple[Any, Any]]:
    outputs: dict[str, tuple[Any, Any]] = {}
    for child in list(getattr(container, "Group", []) or []):
        if str(getattr(child, "TypeId", "")) != "PartDesign::Body":
            continue
        key = str(getattr(child, PROP_OUTPUT_KEY, "") or "")
        if not key:
            continue
        feature = None
        for item in list(getattr(child, "Group", []) or []):
            if str(getattr(item, PROP_OUTPUT_KEY, "") or "") == key:
                feature = item
                break
        if feature is not None:
            outputs[key] = (child, feature)
    return outputs


def _set_shaded_display(obj: Any) -> None:
    view = getattr(obj, "ViewObject", None)
    if view is None:
        # Headless sessions (FreeCADCmd) have no view providers; the display
        # contract only applies when a GUI is attached.
        return
    modes = list(view.listDisplayModes())
    if "Shaded" not in modes:
        raise RuntimeError(
            f"OpenSCAD output {obj.Name} cannot use Shaded display mode. "
            f"Available modes: {modes}"
        )
    if str(view.DisplayMode) != "Shaded":
        view.DisplayMode = "Shaded"


def restore_output_display_modes(doc: Any) -> list[str]:
    """Restore the edge-free display contract for accepted OpenSCAD outputs."""
    restored: list[str] = []
    for container in _model_objects(doc):
        for body, feature in _output_objects(container).values():
            for obj in (body, feature):
                if getattr(obj, "ViewObject", None) is None:
                    continue
                _set_shaded_display(obj)
                restored.append(str(obj.Name))
    return restored


def inspect_model(service: Any, model_id: str) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
    try:
        root = _project_root(service)
        contract = _artifact_contract(root, model_id)
        container = _find_model(doc, model_id)
    except OpenSCADFailure as exc:
        return exc.payload
    if contract is None and container is None:
        return _failure(
            "MODEL_NOT_FOUND",
            "precondition",
            f"No OpenSCAD model has id {model_id!r}.",
            observed={"available_models": model_summaries(doc, root)},
        )
    model = (
        _artifact_summary(contract, include_source=True)
        if contract is not None
        else _model_summary(container, include_source=True)
    )
    if container is not None:
        model["object_name"] = container.Name
        accepted_outputs = []
        for key, (body, feature) in _output_objects(container).items():
            accepted_outputs.append(
                {
                    "key": key,
                    "body": body.Name,
                    "feature": feature.Name,
                    "fidelity": str(getattr(feature, PROP_FIDELITY, "") or ""),
                    "shape": _shape_facts(feature.Shape),
                }
            )
        model["accepted_outputs"] = accepted_outputs
    if contract is not None:
        model["artifact_directory"] = str(contract["directory"])
        model["latest_attempt"] = contract["latest_attempt"]
        accepted = contract["accepted_revision"]
        if accepted and accepted != contract["working_revision"]:
            accepted_path = contract["directory"] / "revisions" / f"{accepted}.scad"
            if accepted_path.is_file():
                model["accepted_source"] = accepted_path.read_text(encoding="utf-8")
    return {"ok": True, "model": model, "cad_revision": service.structural_document_revision()}


def _apply_source_edits(source: str, edits: Any) -> str:
    if not isinstance(edits, list) or not edits:
        raise OpenSCADFailure(
            _failure("EMPTY_SOURCE_EDITS", "schema", "edits must contain at least one replacement.")
        )
    updated = source
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise OpenSCADFailure(
                _failure("INVALID_SOURCE_EDIT", "schema", f"edits[{index}] must be an object.")
            )
        old = str(edit.get("old_text") or "")
        new = str(edit.get("new_text") or "")
        if not old:
            raise OpenSCADFailure(
                _failure("INVALID_SOURCE_EDIT", "schema", f"edits[{index}].old_text cannot be empty.")
            )
        count = updated.count(old)
        if count != 1:
            raise OpenSCADFailure(
                _failure(
                    "SOURCE_EDIT_MATCH_COUNT",
                    "precondition",
                    f"edits[{index}].old_text matched {count} times; exactly one match is required.",
                    observed={"edit_index": index, "match_count": count},
                    required_changes=[{"inspect_model": True, "use_exact_current_text": True}],
                )
            )
        updated = updated.replace(old, new, 1)
    validate_source(updated)
    return updated


def _merge_patch(target: Any, patch: Any) -> Any:
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _merge_patch(result.get(key), value)
    return result


def _prepared_runtime_version(prepared: dict[str, Any]) -> str:
    health = prepared.get("health")
    if not isinstance(health, dict):
        raise RuntimeError("Prepared OpenSCAD execution has no runtime health data.")
    version = str(health.get("runtime_version") or "").strip()
    if not version:
        raise RuntimeError("Prepared OpenSCAD execution has no runtime version.")
    return version


def _persist_working_candidate(prepared: dict[str, Any]) -> dict[str, str]:
    directory = _model_directory(prepared["project_root"], prepared["model_id"])
    directory.mkdir(parents=True, exist_ok=True)
    attempts = directory / "attempts" / prepared["revision"]
    attempts.mkdir(parents=True, exist_ok=True)
    previous_names: list[str] = []
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        previous_names = _manifest_source_names(_read_json(manifest_path, "OpenSCAD manifest"))
    _write_project_source_files(directory, prepared["source_files"], previous_names)
    _write_json(directory / "parameters.json", prepared["parameters"])
    _write_project_source_files(attempts, prepared["source_files"])
    _write_json(attempts / "parameters.json", prepared["parameters"])
    manifest = {
        "schema": MODEL_SCHEMA,
        "model_id": prepared["model_id"],
        "label": prepared["model_name"],
        "state": "working",
        "revision": prepared["revision"],
        "working_revision": prepared["revision"],
        "accepted_revision": prepared["accepted_revision_before"],
        "runtime_version": _prepared_runtime_version(prepared),
        "conversion_mode": prepared["conversion_mode"],
        "source_files": sorted(prepared["source_files"]),
        "outputs": prepared["accepted_outputs"],
        "output_facts": prepared["accepted_output_facts"],
        "fidelity": prepared["accepted_fidelity"],
        "latest_attempt": {
            "revision": prepared["revision"],
            "status": "working",
            "path": str(Path("attempts") / prepared["revision"]),
        },
    }
    _write_json(directory / "manifest.json", manifest)
    _write_json(
        attempts / "manifest.json",
        {
            "schema": ATTEMPT_SCHEMA,
            "model_id": prepared["model_id"],
            "revision": prepared["revision"],
            "status": "working",
            "conversion_mode": prepared["conversion_mode"],
            "created_at": time.time(),
        },
    )
    return {
        "directory": str(directory),
        "source": str(attempts / "model.scad"),
        "working_source": str(directory / "model.scad"),
        "parameters": str(directory / "parameters.json"),
        "attempt": str(attempts),
    }


def stage_editor_source(
    service: Any,
    model_id: str,
    expected_revision: str,
    source: str,
    conversion_mode: str,
) -> dict[str, Any]:
    directory = _model_directory(_project_root(service), model_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise OpenSCADFailure(
            _failure("MODEL_NOT_FOUND", "precondition", f"No OpenSCAD model has id {model_id!r}.")
        )
    manifest = _read_json(manifest_path, "OpenSCAD manifest")
    current_source = (directory / "model.scad").read_text(encoding="utf-8")
    source_files = _read_project_source_files(directory, manifest, current_source)
    source_files["model.scad"] = source
    return stage_editor_files(
        service,
        model_id,
        expected_revision,
        source_files,
        conversion_mode,
    )


def stage_editor_files(
    service: Any,
    model_id: str,
    expected_revision: str,
    source_files: dict[str, str],
    conversion_mode: str,
) -> dict[str, Any]:
    project_root = _project_root(service)
    directory = _model_directory(project_root, model_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise OpenSCADFailure(
            _failure("MODEL_NOT_FOUND", "precondition", f"No OpenSCAD model has id {model_id!r}.")
        )
    manifest = _read_json(manifest_path, "OpenSCAD manifest")
    current_revision = str(manifest.get("working_revision") or manifest.get("revision") or "")
    if current_revision != str(expected_revision or ""):
        raise OpenSCADFailure(
            _failure(
                "STALE_MODEL_REVISION",
                "precondition",
                "The OpenSCAD source changed after the editor loaded it.",
                requested={"expected_revision": expected_revision},
                observed={"current_revision": current_revision},
            )
        )
    source = str(source_files.get("model.scad") or "")
    files = clean_source_files(source_files, source)
    parameters = clean_parameters(_read_json(directory / "parameters.json", "OpenSCAD parameters"))
    mode = clean_conversion_mode(conversion_mode)
    revision = source_revision(source, parameters, files, mode)
    if revision == current_revision:
        return {
            "ok": True,
            "changed": False,
            "working_revision": revision,
            "conversion_mode": mode,
        }
    previous_names = _manifest_source_names(manifest)
    _write_project_source_files(directory, files, previous_names)
    manifest.update(
        {
            "state": "working",
            "revision": revision,
            "working_revision": revision,
            "conversion_mode": mode,
            "source_files": sorted(files),
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
    _write_project_source_files(attempt, files)
    _write_json(attempt / "parameters.json", parameters)
    _write_json(
        attempt / "manifest.json",
        {
            "schema": ATTEMPT_SCHEMA,
            "model_id": model_id,
            "revision": revision,
            "status": "working",
            "conversion_mode": mode,
            "created_at": time.time(),
        },
    )
    return {
        "ok": True,
        "changed": True,
        "working_revision": revision,
        "conversion_mode": mode,
    }


def revert_working_to_accepted(service: Any, model_id: str) -> dict[str, Any]:
    project_root = _project_root(service)
    contract = _artifact_contract(project_root, model_id)
    if contract is None:
        raise OpenSCADFailure(
            _failure("MODEL_NOT_FOUND", "precondition", f"No OpenSCAD model has id {model_id!r}.")
        )
    accepted = contract["accepted_revision"]
    if not accepted:
        raise OpenSCADFailure(
            _failure("NO_ACCEPTED_REVISION", "precondition", "This OpenSCAD model has no accepted revision to restore.")
        )
    directory = contract["directory"]
    revision_directory = directory / "revisions" / accepted
    if revision_directory.is_dir():
        source_path = revision_directory / "model.scad"
        parameters_path = revision_directory / "parameters.json"
        source_manifest = _read_json(
            revision_directory / "sources.json", "accepted OpenSCAD source manifest"
        )
    else:
        source_path = directory / "revisions" / f"{accepted}.scad"
        parameters_path = directory / "revisions" / f"{accepted}.parameters.json"
        source_manifest = {
            "source_files": ["model.scad"],
            "conversion_mode": contract["conversion_mode"],
        }
    if not source_path.is_file() or not parameters_path.is_file():
        raise OpenSCADFailure(
            _failure("ACCEPTED_REVISION_MISSING", "document_state", "The accepted OpenSCAD revision files are missing.")
        )
    source = source_path.read_text(encoding="utf-8")
    parameters = _read_json(parameters_path, "accepted OpenSCAD parameters")
    conversion_mode = clean_conversion_mode(source_manifest.get("conversion_mode"))
    source_files = (
        _read_project_source_files(revision_directory, source_manifest, source)
        if revision_directory.is_dir()
        else {"model.scad": source}
    )
    _write_project_source_files(
        directory,
        source_files,
        _manifest_source_names(contract["manifest"]),
    )
    _write_json(directory / "parameters.json", parameters)
    manifest = dict(contract["manifest"])
    manifest.update(
        {
            "state": "accepted",
            "revision": accepted,
            "working_revision": accepted,
            "conversion_mode": conversion_mode,
            "source_files": sorted(source_files),
            "latest_attempt": {
                "revision": accepted,
                "status": "accepted",
                "path": str(Path("attempts") / accepted),
            },
        }
    )
    _write_json(directory / "manifest.json", manifest)
    return {
        "ok": True,
        "model_id": model_id,
        "working_revision": accepted,
        "conversion_mode": conversion_mode,
        "source": source,
    }


def record_failed_attempt(prepared: dict[str, Any], failure: dict[str, Any]) -> dict[str, Any]:
    directory = _model_directory(prepared["project_root"], prepared["model_id"])
    attempt = directory / "attempts" / prepared["revision"]
    _write_json(attempt / "failure.json", failure)
    attempt_manifest = _read_json(attempt / "manifest.json", "OpenSCAD attempt")
    attempt_manifest.update({"status": "failed", "finished_at": time.time()})
    _write_json(attempt / "manifest.json", attempt_manifest)
    manifest = _read_json(directory / "manifest.json", "OpenSCAD manifest")
    manifest["state"] = "failed"
    manifest["latest_attempt"] = {
        "revision": prepared["revision"],
        "status": "failed",
        "path": str(Path("attempts") / prepared["revision"]),
        "failure": failure,
    }
    _write_json(directory / "manifest.json", manifest)
    return {
        "model_id": prepared["model_id"],
        "working_revision": prepared["revision"],
        "accepted_revision": prepared["accepted_revision_before"],
        "source_path": str(directory / "model.scad"),
    }


def prepare_execution(service: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    health = runtime_health()
    if not health.get("ready"):
        raise OpenSCADFailure(
            _failure(
                "RUNTIME_UNAVAILABLE",
                "runtime",
                str(health.get("error") or "OpenSCAD runtime is unavailable."),
                observed=health,
            )
        )
    doc = service._active_document()
    if doc is None:
        raise OpenSCADFailure(_failure("NO_DOCUMENT", "precondition", "No active FreeCAD document."))
    project_root = _project_root(service)
    operation = str(tool_name or "").strip()
    creating = operation == "openscad.create_model"
    if creating:
        model_name = str(arguments.get("model_name") or "").strip()
        if not _NAME_PATTERN.fullmatch(model_name):
            raise OpenSCADFailure(
                _failure(
                    "INVALID_MODEL_NAME",
                    "schema",
                    "model_name must start with a letter and contain at most 96 letters, numbers, spaces, dots, underscores, or hyphens.",
                )
            )
        source = str(arguments.get("source") or "")
        parameters = clean_parameters(arguments.get("parameters"))
        conversion_mode = clean_conversion_mode(arguments.get("conversion_mode"))
        source_files = clean_source_files(arguments.get("source_files"), source)
        duplicates = [item for item in model_summaries(doc, project_root) if item.get("label") == model_name]
        if duplicates:
            raise OpenSCADFailure(
                _failure(
                    "MODEL_NAME_EXISTS",
                    "precondition",
                    "An OpenSCAD model with this label already exists; inspect and edit it instead of creating a duplicate.",
                    observed={"matches": duplicates},
                )
            )
        model_id = uuid.uuid4().hex
        base_revision = ""
        accepted_revision_before = ""
        accepted_outputs: dict[str, Any] = {}
        accepted_output_facts: dict[str, Any] = {}
        accepted_fidelity = ""
    else:
        model_id = str(arguments.get("model_id") or "").strip().lower()
        contract = _artifact_contract(project_root, model_id)
        target = _find_model(doc, model_id)
        if contract is None:
            raise OpenSCADFailure(
                _failure(
                    "MODEL_NOT_FOUND",
                    "precondition",
                    f"No persisted OpenSCAD model has id {model_id!r}.",
                    observed={"available_models": model_summaries(doc, project_root)},
                )
            )
        base_revision = contract["working_revision"]
        expected_revision = str(arguments.get("expected_revision") or "").strip()
        if expected_revision != base_revision:
            raise OpenSCADFailure(
                _failure(
                    "STALE_MODEL_REVISION",
                    "precondition",
                    "The OpenSCAD model changed after it was inspected.",
                    requested={"expected_revision": expected_revision},
                    observed={"current_revision": base_revision},
                    required_changes=[{"inspect_model": model_id}],
                )
            )
        accepted_revision_before = contract["accepted_revision"]
        if accepted_revision_before and target is None:
            raise OpenSCADFailure(
                _failure(
                    "ACCEPTED_MODEL_OBJECT_MISSING",
                    "document_state",
                    "Accepted OpenSCAD geometry is missing from the FreeCAD document.",
                    observed={"model_id": model_id, "accepted_revision": accepted_revision_before},
                )
            )
        model_name = contract["model_name"]
        source = contract["source"]
        source_files = contract["source_files"]
        parameters = contract["parameters"]
        conversion_mode = contract["conversion_mode"]
        accepted_outputs = contract["outputs"]
        accepted_output_facts = contract["output_facts"]
        accepted_fidelity = contract["fidelity"]
        if operation == "openscad.edit_source":
            source_file = _clean_source_path(arguments.get("source_file"))
            if source_file not in source_files:
                raise OpenSCADFailure(
                    _failure(
                        "SOURCE_FILE_NOT_FOUND",
                        "precondition",
                        f"OpenSCAD project source {source_file!r} is not tracked by this model.",
                        observed={"source_files": sorted(source_files)},
                    )
                )
            source_files[source_file] = _apply_source_edits(
                source_files[source_file], arguments.get("edits")
            )
            source = source_files["model.scad"]
        elif operation == "openscad.set_parameters":
            patch = _json_object(arguments.get("patch"), "patch")
            if not patch:
                raise OpenSCADFailure(_failure("EMPTY_PARAMETER_PATCH", "schema", "patch cannot be empty."))
            parameters = clean_parameters(_merge_patch(parameters, patch))
        elif operation == "openscad.set_conversion_mode":
            conversion_mode = clean_conversion_mode(arguments.get("conversion_mode"))
        elif operation == "openscad.editor_rebuild":
            pass
        else:
            raise OpenSCADFailure(
                _failure("UNSUPPORTED_OPENSCAD_TOOL", "surface", f"Unsupported OpenSCAD operation: {operation}")
            )
    source_files["model.scad"] = source
    source_files = clean_source_files(source_files, source)
    revision = source_revision(source, parameters, source_files, conversion_mode)
    if (
        not creating
        and revision == base_revision
        and operation != "openscad.editor_rebuild"
    ):
        raise OpenSCADFailure(
            _failure(
                "NO_MODEL_CHANGE",
                "precondition",
                "The requested OpenSCAD edit produces the current revision.",
                observed={"revision": revision},
            )
        )
    freecadcmd_executable = _freecadcmd_executable()
    staging = project_root / "openscad" / ".staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=False)
    prepared = {
        "model_id": model_id,
        "creating": creating,
        "operation": operation,
        "model_name": model_name,
        "source": source,
        "source_files": source_files,
        "parameters": parameters,
        "conversion_mode": conversion_mode,
        "revision": revision,
        "base_revision": base_revision,
        "accepted_revision_before": accepted_revision_before,
        "accepted_outputs": accepted_outputs,
        "accepted_output_facts": accepted_output_facts,
        "accepted_fidelity": accepted_fidelity,
        "project_root": str(project_root),
        "staging": str(staging),
        "health": health,
        "freecadcmd_executable": str(freecadcmd_executable),
        "document_name": doc.Name,
        "cad_revision_before": service.structural_document_revision(),
    }
    try:
        prepared["artifacts"] = _persist_working_candidate(prepared)
        shutil.copy2(_module_root() / "openscad_freecad_worker.py", staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return prepared


def _scad_literal(value: Any) -> str:
    if value is None:
        return "undef"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list):
        return "[" + ",".join(_scad_literal(item) for item in value) + "]"
    raise TypeError(f"Unsupported OpenSCAD value: {type(value).__name__}")


def _library_paths(prepared: dict[str, Any]) -> list[Path]:
    settings = load_settings()
    model_directory = _model_directory(prepared["project_root"], prepared["model_id"])
    candidates = [
        model_directory,
        Path(prepared["project_root"]) / "openscad" / "libraries",
    ]
    for line in str(settings.openscad_library_paths or "").splitlines():
        clean = line.strip()
        if clean:
            candidates.append(Path(clean).expanduser())
    candidates.append(bundled_runtime_root() / "libraries")
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate.absolute())
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _runner_environment(prepared: dict[str, Any]) -> dict[str, str]:
    staging = Path(prepared["staging"])
    preserved = ("COMSPEC", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "PATH", "PATHEXT", "SystemRoot", "WINDIR")
    environment = {name: os.environ[name] for name in preserved if str(os.environ.get(name) or "").strip()}
    environment.update(
        {
            "HOME": str(staging),
            "OPENSCADPATH": os.pathsep.join(str(path) for path in _library_paths(prepared)),
            "TEMP": str(staging),
            "TMP": str(staging),
            "TMPDIR": str(staging),
        }
    )
    return _augment_runtime_environment(
        environment, Path(str(prepared["health"]["executable"]))
    )


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    try:
        if sys.platform != "win32":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=3.0)
    except Exception:
        process.kill()
        process.wait(timeout=3.0)


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


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    cancellation_check: Callable[[], bool] | None,
    deadline: float,
    memory_limit_bytes: int = 0,
) -> dict[str, Any]:
    creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=sys.platform != "win32",
        creationflags=creation_flags,
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
        if now >= deadline:
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
        _terminate_process(process)
    stdout, stderr = process.communicate()
    return {
        "returncode": process.returncode,
        "stdout": stdout[-16000:],
        "stderr": stderr[-16000:],
        "cancelled": cancelled,
        "timed_out": timed_out,
        "memory_exceeded": memory_exceeded,
        "observed_memory_bytes": observed_memory,
    }


def _compiler_command(prepared: dict[str, Any], output: Path) -> list[str]:
    command = [str(prepared["health"]["executable"]), "--hardwarnings"]
    for name, value in sorted(prepared["parameters"].items()):
        command.extend(["-D", f"{name}={_scad_literal(value)}"])
    command.extend(["-o", str(output), str(Path(prepared["artifacts"]["source"]))])
    return command


def _converter_command(prepared: dict[str, Any], mode: str, input_path: Path) -> tuple[list[str], dict[str, str]]:
    staging = Path(prepared["staging"])
    request = {
        "mode": mode,
        "input_path": str(input_path),
        "output_path": str(staging / "model.brep"),
        "result_path": str(staging / "conversion.json"),
        "openscad_executable": str(prepared["health"]["executable"]),
        "csg_text_path": str(staging / "model.csg"),
    }
    _write_json(staging / "conversion-request.json", request)
    environment = _runner_environment(prepared)
    environment["VIBECAD_OPENSCAD_CONVERSION_REQUEST"] = str(staging / "conversion-request.json")
    environment["VIBECAD_OPENSCAD_CONVERSION_WORKER"] = str(staging / "openscad_freecad_worker.py")
    code = (
        "import os,runpy;"
        "runpy.run_path(os.environ['VIBECAD_OPENSCAD_CONVERSION_WORKER'],run_name='__main__')"
    )
    return [str(prepared["freecadcmd_executable"]), "--safe-mode", "-c", code], environment


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
    staging = Path(prepared["staging"])
    started = time.monotonic()
    deadline = started + timeout_seconds
    environment = _runner_environment(prepared)
    requested_mode = clean_conversion_mode(prepared.get("conversion_mode"))
    exact = requested_mode == "exact_brep"
    source_output = staging / ("model.csg" if exact else "model.stl")
    try:
        compiler = _run_process(
            _compiler_command(prepared, source_output),
            cwd=staging,
            environment=environment,
            cancellation_check=cancellation_check,
            deadline=deadline,
            memory_limit_bytes=memory_limit_bytes,
        )
    except Exception as exc:
        return _failure(
            "OPENSCAD_START_FAILED",
            "execution",
            f"OpenSCAD could not start: {exc}",
            observed={"executable": prepared["health"].get("executable")},
        )
    if compiler["cancelled"]:
        return _failure("RUN_CANCELLED", "execution", "OpenSCAD execution was cancelled.", observed=compiler)
    if compiler["timed_out"]:
        return _failure("EXECUTION_TIMEOUT", "execution", f"OpenSCAD exceeded {timeout_seconds:.0f} seconds.", observed=compiler)
    if compiler.get("memory_exceeded"):
        return _failure(
            "MEMORY_LIMIT_EXCEEDED",
            "execution",
            "OpenSCAD exceeded the "
            f"{memory_limit_bytes // (1024 * 1024)} MB memory budget.",
            observed={
                "memory_limit_bytes": memory_limit_bytes,
                "observed_memory_bytes": compiler.get("observed_memory_bytes"),
                "compiler": compiler,
            },
            required_changes=[
                {"reduce_model_memory_or_increase_memory_budget_preference": True}
            ],
        )
    if compiler["returncode"] != 0 or not source_output.is_file():
        return _failure(
            "OPENSCAD_COMPILE_FAILED" if exact else "OPENSCAD_RENDER_FAILED",
            "execution",
            "OpenSCAD rejected the source."
            if exact
            else "OpenSCAD could not render the requested faceted geometry.",
            observed={
                "conversion_mode": requested_mode,
                "compiler": compiler,
                "diagnostics": _parse_diagnostics(compiler["stderr"]),
            },
            required_changes=[{"edit_source_at_diagnostics": True}],
        )

    worker_mode = "csg" if exact else "mesh"
    converter_command, converter_environment = _converter_command(
        prepared, worker_mode, source_output
    )
    try:
        converter = _run_process(
            converter_command,
            cwd=staging,
            environment=converter_environment,
            cancellation_check=cancellation_check,
            deadline=deadline,
            memory_limit_bytes=memory_limit_bytes,
        )
    except Exception as exc:
        return _failure(
            "OPENSCAD_CONVERTER_START_FAILED",
            "conversion",
            f"The isolated OpenSCAD geometry converter could not start: {exc}",
            observed={"conversion_mode": requested_mode},
        )
    conversion_path = staging / "conversion.json"
    conversion = _read_optional_result(conversion_path)
    if converter["cancelled"]:
        return _failure(
            "RUN_CANCELLED",
            "conversion",
            "OpenSCAD geometry conversion was cancelled.",
            observed=converter,
        )
    if converter["timed_out"]:
        return _failure(
            "EXECUTION_TIMEOUT",
            "conversion",
            f"OpenSCAD geometry conversion exceeded {timeout_seconds:.0f} seconds.",
            observed=converter,
        )
    if converter.get("memory_exceeded"):
        return _failure(
            "MEMORY_LIMIT_EXCEEDED",
            "conversion",
            "OpenSCAD geometry conversion exceeded the "
            f"{memory_limit_bytes // (1024 * 1024)} MB memory budget.",
            observed={
                "memory_limit_bytes": memory_limit_bytes,
                "observed_memory_bytes": converter.get("observed_memory_bytes"),
                "converter": converter,
            },
            required_changes=[
                {"reduce_model_memory_or_increase_memory_budget_preference": True}
            ],
        )
    if converter["returncode"] != 0 or not conversion.get("ok"):
        exact_required_changes = (
            [
                {"inspect_conversion_diagnostics": True},
                {"retry_with_conversion_mode": "faceted_brep"},
            ]
            if exact
            else [{"inspect_conversion_diagnostics": True}]
        )
        return _failure(
            "OPENSCAD_EXACT_CONVERSION_FAILED"
            if exact
            else "OPENSCAD_FACETED_CONVERSION_FAILED",
            "conversion",
            str(conversion.get("error") or "OpenSCAD geometry conversion failed."),
            observed={
                "conversion_mode": requested_mode,
                "converter": converter,
                "conversion": conversion,
            },
            required_changes=exact_required_changes,
        )
    conversion["ok"] = True
    conversion["elapsed_seconds"] = time.monotonic() - started
    conversion["compiler"] = compiler
    conversion["conversion_mode"] = requested_mode
    conversion["conversion_backend"] = worker_mode
    conversion["source_artifact_path"] = str(source_output)
    conversion["openscad_version"] = _prepared_runtime_version(prepared)
    return conversion


def _read_optional_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


_DIAGNOSTIC_PATTERN = re.compile(
    r"^(?P<level>ERROR|WARNING):\s*(?P<message>.*?)(?:\s+in file\s+(?P<file>.*?),\s+line\s+(?P<line>\d+))?$",
    re.IGNORECASE,
)


def _parse_diagnostics(stderr: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for raw_line in str(stderr or "").splitlines():
        line = raw_line.strip()
        match = _DIAGNOSTIC_PATTERN.match(line)
        if not match:
            continue
        diagnostics.append(
            {
                "severity": match.group("level").lower(),
                "message": match.group("message").strip(),
                "file": (match.group("file") or "").strip(),
                "line": int(match.group("line")) if match.group("line") else None,
            }
        )
    return diagnostics


def _shape_facts(shape: Any) -> dict[str, Any]:
    if shape is None or bool(shape.isNull()):
        return {
            "valid": False,
            "is_null": True,
            "shape_type": "Null",
            "solids": 0,
            "faces": 0,
            "edges": 0,
            "vertices": 0,
            "volume_mm3": 0.0,
            "area_mm2": 0.0,
            "state": "empty_shape",
        }
    box = shape.BoundBox
    return {
        "valid": bool(shape.isValid()),
        "is_null": False,
        "shape_type": str(shape.ShapeType),
        "solids": len(shape.Solids),
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "vertices": len(shape.Vertexes),
        "volume_mm3": float(shape.Volume),
        "area_mm2": float(shape.Area),
        "bbox": {
            "min": [float(box.XMin), float(box.YMin), float(box.ZMin)],
            "max": [float(box.XMax), float(box.YMax), float(box.ZMax)],
            "size": [float(box.XLength), float(box.YLength), float(box.ZLength)],
        },
    }


_OUTPUT_KEY_PATTERN = re.compile(r"^Solid (\d+)$")
_BBOX_TOLERANCE_MM = 1.0e-6
_SCALAR_RELATIVE_TOLERANCE = 1.0e-6


def _output_key_ordinal(key: str) -> int | None:
    match = _OUTPUT_KEY_PATTERN.match(str(key))
    return int(match.group(1)) if match else None


def _output_key_sort(key: str) -> tuple[bool, int, str]:
    ordinal = _output_key_ordinal(key)
    return (ordinal is None, ordinal if ordinal is not None else 0, str(key))


def _scalars_match(left: float, right: float) -> bool:
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= _SCALAR_RELATIVE_TOLERANCE * scale


def _bboxes_match(left: Any, right: Any) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    for corner in ("min", "max"):
        first = left.get(corner)
        second = right.get(corner)
        if (
            not isinstance(first, (list, tuple))
            or not isinstance(second, (list, tuple))
            or len(first) != 3
            or len(second) != 3
        ):
            return False
        for a, b in zip(first, second):
            if abs(float(a) - float(b)) > _BBOX_TOLERANCE_MM:
                return False
    return True


def _solid_facts_match(new_facts: dict[str, Any], prior_facts: dict[str, Any]) -> bool:
    for count_key in ("solids", "faces", "edges", "vertices"):
        if int(new_facts.get(count_key) or 0) != int(prior_facts.get(count_key) or 0):
            return False
    for scalar_key in ("volume_mm3", "area_mm2"):
        if not _scalars_match(
            float(new_facts.get(scalar_key) or 0.0),
            float(prior_facts.get(scalar_key) or 0.0),
        ):
            return False
    return _bboxes_match(new_facts.get("bbox"), prior_facts.get("bbox"))


def match_output_keys(
    new_facts: list[dict[str, Any]],
    accepted_output_facts: dict[str, Any],
) -> list[str]:
    """Assign stable output keys to freshly imported solids.

    Solids that are geometrically identical to an accepted output keep that
    output's key so untouched solids retain their FreeCAD bodies across
    edits.  Remaining solids fall back to the prior positional pairing (so a
    single edited solid keeps its key), and only solids in excess of the
    accepted outputs receive brand-new keys.  Accepted keys left unassigned
    are simply absent from the result, which makes commit_outputs remove
    their bodies exactly as before.
    """
    prior_facts: dict[str, dict[str, Any]] = {}
    for key, value in (accepted_output_facts or {}).items():
        shape = value.get("shape") if isinstance(value, dict) else None
        prior_facts[str(key)] = shape if isinstance(shape, dict) else {}

    assigned: list[str | None] = [None] * len(new_facts)
    unused_prior = dict(prior_facts)
    # Pass 1: geometrically identical solids retain their accepted keys.
    for index, facts in enumerate(new_facts):
        for key in sorted(unused_prior, key=_output_key_sort):
            if _solid_facts_match(facts, unused_prior[key]):
                assigned[index] = key
                del unused_prior[key]
                break
    # Pass 2: pair remaining solids with remaining accepted keys in ordinal
    # order, matching the historical positional assignment exactly when no
    # solid was recognised in pass 1.
    remaining_keys = sorted(unused_prior, key=_output_key_sort)
    remaining_indices = [index for index, key in enumerate(assigned) if key is None]
    for index, key in zip(remaining_indices, remaining_keys):
        assigned[index] = key
    # Pass 3: brand-new solids get fresh ordinal keys that never recycle a
    # key seen during this edit.
    used = {key for key in assigned if key is not None} | set(prior_facts)
    ordinals = [_output_key_ordinal(key) for key in used]
    next_ordinal = max((value for value in ordinals if value is not None), default=0) + 1
    for index, key in enumerate(assigned):
        if key is not None:
            continue
        candidate = f"Solid {next_ordinal:03d}"
        while candidate in used:
            next_ordinal += 1
            candidate = f"Solid {next_ordinal:03d}"
        assigned[index] = candidate
        used.add(candidate)
        next_ordinal += 1
    return [key for key in assigned if key is not None]


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


def import_validated_outputs(prepared: dict[str, Any], execution: dict[str, Any]) -> list[dict[str, Any]]:
    import Part

    output_path = Path(str(execution.get("output_path") or "")).resolve()
    staging = Path(prepared["staging"]).resolve()
    if staging not in output_path.parents or not output_path.is_file():
        raise OpenSCADFailure(
            _failure("OUTPUT_FILE_INVALID", "import", "OpenSCAD converter returned no valid BREP output.")
        )
    try:
        shape = Part.read(str(output_path))
    except Exception as exc:
        raise OpenSCADFailure(
            _failure("BREP_IMPORT_FAILED", "import", f"FreeCAD could not read converted OpenSCAD geometry: {exc}")
        ) from exc
    solids = list(shape.Solids)
    solids.sort(
        key=lambda item: (
            round(float(item.BoundBox.XMin), 9),
            round(float(item.BoundBox.YMin), 9),
            round(float(item.BoundBox.ZMin), 9),
            round(float(item.Volume), 9),
        )
    )
    if not solids:
        raise OpenSCADFailure(
            _failure(
                "NO_SOLID_OUTPUT",
                "import",
                "OpenSCAD source did not produce a valid three-dimensional solid.",
                observed={"shape": _shape_facts(shape)},
            )
        )
    fidelity = str(execution.get("fidelity") or "").strip()
    expected_fidelity = clean_conversion_mode(prepared.get("conversion_mode"))
    if fidelity not in _Fidelity:
        raise OpenSCADFailure(
            _failure(
                "CONVERSION_FIDELITY_MISSING",
                "import",
                "The OpenSCAD converter did not report a valid geometry fidelity.",
                observed={"fidelity": fidelity},
            )
        )
    if fidelity != expected_fidelity:
        raise OpenSCADFailure(
            _failure(
                "CONVERSION_FIDELITY_MISMATCH",
                "import",
                f"Requested {expected_fidelity}, but the converter produced {fidelity}.",
                requested={"conversion_mode": expected_fidelity},
                observed={"fidelity": fidelity},
                required_changes=[{"select_conversion_mode": fidelity}],
            )
        )
    facts_list = [_shape_facts(solid) for solid in solids]
    keys = match_output_keys(facts_list, prepared.get("accepted_output_facts") or {})
    return [
        {
            "key": key,
            "shape": solid,
            "fidelity": fidelity,
            "shape_facts": facts,
        }
        for key, solid, facts in zip(keys, solids, facts_list)
    ]


def _add_string_property(obj: Any, name: str, group: str = "OpenSCAD") -> None:
    if name not in list(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty("App::PropertyString", name, group)


def _safe_internal_name(value: str, prefix: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "")).strip("_")
    if not clean or not clean[0].isalpha():
        clean = f"{prefix}_{clean}" if clean else prefix
    return clean[:80]


def _accepted_manifest(
    prepared: dict[str, Any],
    execution: dict[str, Any],
    container: Any,
    committed: list[dict[str, Any]],
    fidelity: str,
) -> dict[str, Any]:
    directory = _model_directory(prepared["project_root"], prepared["model_id"])
    revisions = directory / "revisions"
    revisions.mkdir(parents=True, exist_ok=True)
    revision = prepared["revision"]
    _write_project_source_files(
        directory,
        prepared["source_files"],
        _manifest_source_names(_read_json(directory / "manifest.json", "OpenSCAD manifest")),
    )
    _write_json(directory / "parameters.json", prepared["parameters"])
    _write_text(revisions / f"{revision}.scad", prepared["source"])
    _write_json(revisions / f"{revision}.parameters.json", prepared["parameters"])
    revision_directory = revisions / revision
    _write_project_source_files(revision_directory, prepared["source_files"])
    _write_json(revision_directory / "parameters.json", prepared["parameters"])
    _write_json(
        revision_directory / "sources.json",
        {
            "source_files": sorted(prepared["source_files"]),
            "conversion_mode": prepared["conversion_mode"],
        },
    )
    artifact_directory = revision_directory / "artifacts"
    artifact_directory.mkdir(parents=True, exist_ok=True)
    persisted_outputs: dict[str, dict[str, Any]] = {}
    execution_artifacts = list(execution.get("output_artifacts") or [])
    if len(execution_artifacts) != len(committed):
        raise RuntimeError(
            "OpenSCAD conversion artifacts do not match the committed output count: "
            f"{len(execution_artifacts)} artifacts for {len(committed)} outputs."
        )
    for committed_item, source_item in zip(committed, execution_artifacts):
        key = committed_item["key"]
        stem = _safe_internal_name(key, "solid").lower()
        persisted: dict[str, Any] = {}
        for artifact_key, extension in (("brep_path", "brep"), ("mesh_path", "stl")):
            source_value = str(source_item.get(artifact_key) or "").strip()
            if not source_value:
                continue
            source_path = Path(source_value)
            if not source_path.is_file():
                raise RuntimeError(f"OpenSCAD conversion artifact is missing: {source_path}")
            destination = artifact_directory / f"{stem}.{extension}"
            shutil.copy2(source_path, destination)
            persisted[extension] = destination.relative_to(directory).as_posix()
        if "brep" not in persisted:
            raise RuntimeError(f"OpenSCAD output {key} has no persisted BREP artifact.")
        persisted["triangles"] = source_item.get("triangles")
        persisted_outputs[key] = persisted
    source_artifact = Path(str(execution.get("source_artifact_path") or ""))
    persisted_source_artifact = None
    if source_artifact.is_file():
        source_extension = source_artifact.suffix.lower() or ".dat"
        destination = artifact_directory / f"source{source_extension}"
        shutil.copy2(source_artifact, destination)
        persisted_source_artifact = destination.relative_to(directory).as_posix()
    outputs = {
        item["key"]: {"body": item["body"], "feature": item["feature"]}
        for item in committed
    }
    output_facts = {
        item["key"]: {
            "shape": item["shape"],
            "fidelity": item["fidelity"],
            "artifacts": persisted_outputs[item["key"]],
        }
        for item in committed
    }
    manifest = {
        "schema": MODEL_SCHEMA,
        "model_id": prepared["model_id"],
        "label": str(container.Label),
        "state": "accepted",
        "revision": revision,
        "working_revision": revision,
        "accepted_revision": revision,
        "runtime_version": _prepared_runtime_version(prepared),
        "conversion_mode": prepared["conversion_mode"],
        "source_files": sorted(prepared["source_files"]),
        "outputs": outputs,
        "output_facts": output_facts,
        "fidelity": fidelity,
        "artifacts": {
            "source": persisted_source_artifact,
            "outputs": persisted_outputs,
        },
        "latest_attempt": {
            "revision": revision,
            "status": "accepted",
            "path": str(Path("attempts") / revision),
        },
    }
    _write_json(directory / "manifest.json", manifest)
    attempt_manifest_path = directory / "attempts" / revision / "manifest.json"
    if attempt_manifest_path.is_file():
        attempt_manifest = _read_json(attempt_manifest_path, "OpenSCAD attempt")
        attempt_manifest.update({"status": "accepted", "finished_at": time.time()})
        _write_json(attempt_manifest_path, attempt_manifest)
    return manifest


def commit_outputs(
    service: Any,
    prepared: dict[str, Any],
    execution: dict[str, Any],
    imported: list[dict[str, Any]],
) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None or doc.Name != prepared["document_name"]:
        raise OpenSCADFailure(
            _failure(
                "DOCUMENT_CHANGED",
                "commit",
                "The active document changed while OpenSCAD was running.",
                observed={"expected_document": prepared["document_name"], "active_document": getattr(doc, "Name", None)},
            )
        )
    if bool(getattr(doc, "Recomputing", False)):
        raise OpenSCADFailure(
            _failure(
                "DOCUMENT_RECOMPUTE_IN_PROGRESS",
                "commit",
                f"Document {doc.Name} is still recomputing; OpenSCAD outputs were not committed.",
                observed={"document": doc.Name, "recomputing": True},
            )
        )
    target = _find_model(doc, prepared["model_id"])
    accepted_before = prepared["accepted_revision_before"]
    if accepted_before:
        if target is None:
            raise OpenSCADFailure(_failure("MODEL_REMOVED", "commit", "The target model was removed during compilation."))
        current = str(getattr(target, PROP_REVISION, "") or "")
        if current != accepted_before:
            raise OpenSCADFailure(
                _failure(
                    "MODEL_CHANGED_DURING_EXECUTION",
                    "commit",
                    "The accepted OpenSCAD model changed during compilation.",
                    observed={"accepted_revision": current, "expected_revision": accepted_before},
                )
            )
    elif target is not None:
        raise OpenSCADFailure(_failure("MODEL_ID_COLLISION", "commit", "The generated model id appeared during compilation."))

    created = target is None
    doc.openTransaction("Accept OpenSCAD model")
    try:
        container = target or doc.addObject("App::Part", _safe_internal_name(prepared["model_name"], "OpenSCADModel"))
        for prop in (
            PROP_MODEL_ID,
            PROP_SOURCE,
            PROP_PARAMETERS,
            PROP_REVISION,
            PROP_RUNTIME_VERSION,
            PROP_OUTPUTS,
            PROP_FIDELITY,
            PROP_CONVERSION_MODE,
        ):
            _add_string_property(container, prop)
        existing = _output_objects(container)
        committed: list[dict[str, Any]] = []
        for item in imported:
            key = item["key"]
            pair = existing.get(key)
            if pair is None:
                body = doc.addObject("PartDesign::Body", _safe_internal_name(key, "OpenSCADBody"))
                container.addObject(body)
                feature = body.newObject("PartDesign::Feature", "OpenSCADFeature")
                for obj in (body, feature):
                    _add_string_property(obj, PROP_MODEL_ID)
                    _add_string_property(obj, PROP_OUTPUT_KEY)
                    _add_string_property(obj, PROP_FIDELITY)
            else:
                body, feature = pair
            body.Label = key
            feature.Label = f"{key} (OpenSCAD)"
            for obj in (body, feature):
                setattr(obj, PROP_MODEL_ID, prepared["model_id"])
                setattr(obj, PROP_OUTPUT_KEY, key)
                setattr(obj, PROP_FIDELITY, item["fidelity"])
            feature.Shape = item["shape"]
            body.Tip = feature
            _set_shaded_display(body)
            _set_shaded_display(feature)
            committed.append(
                {
                    "key": key,
                    "body": body.Name,
                    "feature": feature.Name,
                    "fidelity": item["fidelity"],
                    "shape": item["shape_facts"],
                }
            )
        retained = {item["key"] for item in committed}
        removed = []
        for key, (body, feature) in existing.items():
            if key not in retained:
                removed.append(body.Name)
                delete_contained_objects(doc, [body, feature])
        fidelities = {item["fidelity"] for item in committed}
        overall_fidelity = next(iter(fidelities)) if len(fidelities) == 1 else "mixed"
        container.Label = prepared["model_name"]
        setattr(container, PROP_MODEL_ID, prepared["model_id"])
        setattr(container, PROP_SOURCE, prepared["source"])
        setattr(container, PROP_PARAMETERS, _canonical_json(prepared["parameters"]))
        setattr(container, PROP_REVISION, prepared["revision"])
        setattr(container, PROP_RUNTIME_VERSION, _prepared_runtime_version(prepared))
        setattr(container, PROP_FIDELITY, overall_fidelity)
        setattr(container, PROP_CONVERSION_MODE, prepared["conversion_mode"])
        setattr(
            container,
            PROP_OUTPUTS,
            _canonical_json({item["key"]: {"body": item["body"], "feature": item["feature"]} for item in committed}),
        )
        doc.recompute()
        diagnostics = service.recompute_diagnostics()
        errors = _recompute_errors(diagnostics)
        if errors:
            first = errors[0]
            raise RuntimeError(
                "FreeCAD reported errors while accepting OpenSCAD outputs. "
                f"First: {first.get('code') or 'UNKNOWN'} on "
                f"{first.get('object') or 'unknown object'}: "
                f"{first.get('message') or 'no message'}"
            )
        manifest = _accepted_manifest(
            prepared,
            execution,
            container,
            committed,
            overall_fidelity,
        )
        doc.commitTransaction()
    except Exception as exc:
        doc.abortTransaction()
        if isinstance(exc, OpenSCADFailure):
            raise
        raise OpenSCADFailure(
            _failure(
                "OPENSCAD_COMMIT_FAILED",
                "commit",
                f"OpenSCAD geometry could not be committed: {exc}",
            )
        ) from exc
    return {
        "ok": True,
        "created": created,
        "updated": not created,
        "model": _model_summary(container, include_source=False),
        "outputs": committed,
        "removed_outputs": removed,
        "fidelity": overall_fidelity,
        "manifest": manifest,
        "execution": {
            "elapsed_seconds": execution.get("elapsed_seconds"),
            "openscad_version": execution.get("openscad_version")
            or _prepared_runtime_version(prepared),
            "conversion_mode": execution.get("conversion_mode"),
        },
        "native_diagnostics": diagnostics,
        "cad_revision": service.structural_document_revision(),
    }


def delete_model(service: Any, model_id: str, expected_revision: str, reason: str) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
    try:
        root = _project_root(service)
        contract = _artifact_contract(root, model_id)
        container = _find_model(doc, model_id)
    except OpenSCADFailure as exc:
        return exc.payload
    if contract is None and container is None:
        return _failure("MODEL_NOT_FOUND", "precondition", f"No OpenSCAD model has id {model_id!r}.")
    current_revision = contract["working_revision"] if contract is not None else str(getattr(container, PROP_REVISION, "") or "")
    if expected_revision != current_revision:
        return _failure(
            "STALE_MODEL_REVISION",
            "precondition",
            "The OpenSCAD model changed after it was inspected.",
            requested={"expected_revision": expected_revision},
            observed={"current_revision": current_revision},
        )
    if not str(reason or "").strip():
        return _failure("DELETE_REASON_REQUIRED", "schema", "reason cannot be empty.")
    deleted_objects: list[str] = []
    if container is not None:
        doc.openTransaction("Delete OpenSCAD model")
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
            return _failure("DELETE_FAILED", "commit", f"OpenSCAD model deletion failed: {exc}")
    directory = _model_directory(root, model_id)
    if directory.is_dir():
        shutil.rmtree(directory)
    return {
        "ok": True,
        "model_id": model_id,
        "deleted_objects": deleted_objects,
        "reason": str(reason).strip(),
        "cad_revision": service.structural_document_revision(),
    }


def cleanup_prepared(prepared: dict[str, Any]) -> None:
    staging = Path(str(prepared.get("staging") or ""))
    if staging.name and ".staging" in staging.parts:
        shutil.rmtree(staging, ignore_errors=True)


def measurement_artifact(
    service: Any,
    obj: Any,
    *,
    subelement: str = "",
    preferred_format: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the accepted geometry artifact for one OpenSCAD output object."""
    properties = list(getattr(obj, "PropertiesList", []) or [])
    if PROP_MODEL_ID not in properties or PROP_OUTPUT_KEY not in properties:
        return None
    model_id = str(getattr(obj, PROP_MODEL_ID, "") or "").strip()
    output_key = str(getattr(obj, PROP_OUTPUT_KEY, "") or "").strip()
    if not model_id or not output_key:
        return None
    directory = _model_directory(_project_root(service), model_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return {
            "available": False,
            "required_action": "rebuild_openscad_model",
            "reason": "accepted_manifest_not_found",
        }
    manifest = _read_json(manifest_path, "OpenSCAD manifest")
    output_facts = manifest.get("output_facts")
    if not isinstance(output_facts, dict):
        return {
            "available": False,
            "required_action": "rebuild_openscad_model",
            "reason": "accepted_output_facts_missing",
        }
    output = output_facts.get(output_key)
    if not isinstance(output, dict):
        return {
            "available": False,
            "required_action": "rebuild_openscad_model",
            "reason": "accepted_output_not_found",
        }
    artifacts = output.get("artifacts")
    if not isinstance(artifacts, dict):
        return {
            "available": False,
            "fidelity": str(output.get("fidelity") or manifest.get("fidelity") or ""),
            "required_action": "rebuild_openscad_model",
            "reason": "accepted_artifacts_missing",
        }
    fidelity = str(output.get("fidelity") or manifest.get("fidelity") or "").strip()
    preferred = str(preferred_format or "").strip().lower()
    if preferred not in {"brep", "stl"}:
        preferred = (
            "stl"
            if not str(subelement or "").strip()
            and fidelity == "faceted_brep"
            and str(artifacts.get("stl") or "").strip()
            else "brep"
        )
    relative = str(artifacts.get(preferred) or "").strip()
    if not relative:
        return {
            "available": False,
            "fidelity": fidelity,
            "required_action": "rebuild_openscad_model",
            "reason": f"accepted_{preferred}_artifact_missing",
        }
    path = (directory / relative).resolve()
    if not path.is_file():
        return {
            "available": False,
            "fidelity": fidelity,
            "required_action": "rebuild_openscad_model",
            "reason": f"accepted_{preferred}_artifact_not_found",
            "path": str(path),
        }
    return {
        "available": True,
        "format": preferred,
        "path": str(path),
        "fidelity": fidelity,
        "triangles": artifacts.get("triangles"),
    }


def runtime_execution_smoke(executable_override: str = "") -> dict[str, Any]:
    health = runtime_health(executable_override=executable_override, refresh=True)
    if not health.get("ready"):
        raise RuntimeError(str(health.get("error") or "OpenSCAD runtime unavailable."))
    executable = Path(str(health["executable"]))
    with tempfile.TemporaryDirectory(prefix="vibecad-openscad-smoke-") as temporary:
        root = Path(temporary)
        source = root / "smoke.scad"
        output = root / "smoke.stl"
        source.write_text(
            "difference(){cube([10,8,6],center=true);"
            "cylinder(h=8,r=2,center=true,$fn=32);}\n",
            encoding="utf-8",
        )
        environment = _augment_runtime_environment(os.environ, executable)
        environment["OPENSCADPATH"] = os.pathsep.join(
            [
                str(bundled_runtime_root() / "libraries"),
                str(bundled_runtime_root() / "share" / "openscad" / "libraries"),
            ]
        )
        completed = subprocess.run(
            [str(executable), "--hardwarnings", "-o", str(output), str(source)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=environment,
            creationflags=(
                int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if sys.platform == "win32"
                else 0
            ),
        )
        if completed.returncode != 0 or not output.is_file() or output.stat().st_size < 100:
            raise RuntimeError(
                "OpenSCAD runtime could not render the smoke model: "
                + (completed.stderr or completed.stdout or f"exit {completed.returncode}")[-2000:]
            )
    return {
        "ok": True,
        "version": health["version"],
        "executable": health["executable"],
        "render": "stl",
    }
