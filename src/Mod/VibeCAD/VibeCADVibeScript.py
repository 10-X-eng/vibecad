# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source-parametric FreeCAD modeling engine driven by VibeScript source.

VibeScript source is the model authority. Validated source executes in an
isolated ``FreeCADCmd`` worker, which returns exact BREP outputs for bounded
publication into the live document. Untrusted source never receives the GUI
document or its process; publication, reference rebinding, native recompute,
and rollback are explicit host-controlled stages.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import vibescript_api
import vibescript_executor
import VibeCADReferenceContracts as reference_contracts
import VibeCADScriptedPublication as scripted_publication
from VibeCADScriptedOwnership import (
    delete_owned_model_objects,
    owned_model_objects,
)
from VibeCADTools import tool_failure

VIBESCRIPT_VERSION = "1"
MODEL_SCHEMA = "vibecad-vibescript-model-v1"
ATTEMPT_SCHEMA = "vibecad-vibescript-attempt-v1"
MAX_SOURCE_BYTES = 512_000
MAX_OUTPUTS = 64
DEFAULT_TIMEOUT_SECONDS = vibescript_executor.DEFAULT_MAX_SECONDS
DEFAULT_MAX_OPERATIONS = vibescript_executor.DEFAULT_MAX_OPERATIONS

PROP_MODEL_ID = "VibeCADVibeScriptModelId"
PROP_SOURCE = "VibeCADVibeScriptSource"
PROP_PARAMETERS = "VibeCADVibeScriptParameters"
PROP_REVISION = "VibeCADVibeScriptRevision"
PROP_RUNTIME_VERSION = "VibeCADVibeScriptRuntimeVersion"
PROP_OUTPUTS = "VibeCADVibeScriptOutputs"
PROP_OUTPUT_KEY = "VibeCADVibeScriptOutputKey"
PROP_INTERFACES = "VibeCADVibeScriptInterfaces"
PUBLICATION_ENGINE = "vibescript"

_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_. -]{0,95}$")
_MODEL_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

ALLOWED_IMPORT_ROOTS = vibescript_executor.ALLOWED_IMPORT_ROOTS
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
_DISALLOWED_DOCUMENT_METHODS = frozenset(
    {
        "closeDocument",
        "mergeProject",
        "newDocument",
        "openDocument",
        "restore",
        "save",
        "saveAs",
        "saveCopy",
        "setActiveDocument",
    }
)

#: Builtins reachable inside the sandbox namespace (runtime allowlist).
_SANDBOX_BUILTIN_NAMES = frozenset(vibescript_executor._BUILTIN_ALLOWLIST)

#: Names injected into the script namespace besides builtins.
_NAMESPACE_NAMES = frozenset({"doc", "params", "__name__"}) | frozenset(
    vibescript_api.__all__
)

#: Real Python builtins that would raise NameError inside the sandbox.
#: Reads of these are rejected statically so a script fails at validation
#: time with a line number instead of mid-execution after mutating geometry.
_EXCLUDED_BUILTIN_NAMES = (
    frozenset(vars(builtins)) - _SANDBOX_BUILTIN_NAMES - _NAMESPACE_NAMES
) | vibescript_executor._FRAME_INTERNAL_BUILTINS

_EXECUTION_FAILURE_CODES = {
    "contract_violation": "VIBESCRIPT_CONTRACT_VIOLATION",
    "execution_budget_exceeded": "VIBESCRIPT_BUDGET_EXCEEDED",
    "sketch_validation_failure": "VIBESCRIPT_SKETCH_UNSOLVED",
    "design_assertion_failure": "VIBESCRIPT_DESIGN_ASSERTION_FAILED",
    "syntax_error": "SOURCE_SYNTAX_ERROR",
    "vibescript_api_failure": "VIBESCRIPT_API_FAILED",
}


class VibeScriptFailure(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        self.payload = dict(payload)
        super().__init__(str(payload.get("error") or "VibeScript operation failed."))


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
        "document_state": "precondition",
        "source_validation": "schema",
        "source_edit": "schema",
        "execution": "native_call",
        "contract": "postcondition",
        "commit": "postcondition",
    }
    return tool_failure(
        "vibescript",
        code,
        stage_map.get(stage, "native_call"),
        error,
        requested=requested,
        observed=observed,
        retry_same_call=retry_same_call,
        required_changes=required_changes,
        engine_stage=stage,
        **details,
    )


# ---------------------------------------------------------------------------
# Source policy
# ---------------------------------------------------------------------------


def _script_bound_names(tree: ast.AST) -> frozenset[str]:
    """Names bound anywhere in the script, in any scope.

    Deliberately scope-insensitive: a binding anywhere (assignment, loop or
    comprehension target, function/class/lambda argument, import alias,
    ``except ... as``, ``with ... as``, walrus, match capture) suppresses the
    excluded-builtin check for that name everywhere. This can only make the
    static check more permissive (the runtime NameError still applies to
    genuinely unbound reads); it can never produce a false positive on a
    script-defined shadow.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".", 1)[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
    return frozenset(bound)


def validate_source(source: str) -> None:
    """Reject source that violates the isolated VibeScript worker policy."""
    encoded = str(source or "").encode("utf-8")
    if not encoded:
        raise VibeScriptFailure(
            _failure("SOURCE_REQUIRED", "source_validation", "source is required.")
        )
    if len(encoded) > MAX_SOURCE_BYTES:
        raise VibeScriptFailure(
            _failure(
                "SOURCE_TOO_LARGE",
                "source_validation",
                f"source exceeds {MAX_SOURCE_BYTES} UTF-8 bytes.",
                observed={"source_bytes": len(encoded)},
            )
        )
    try:
        tree = ast.parse(
            source, filename=vibescript_executor.SCRIPT_FILENAME, mode="exec"
        )
    except SyntaxError as exc:
        raise VibeScriptFailure(
            _failure(
                "SOURCE_SYNTAX_ERROR",
                "source_validation",
                str(exc),
                observed={"line": exc.lineno, "column": exc.offset},
            )
        ) from exc
    violations: list[dict[str, Any]] = []
    bound_names = _script_bound_names(tree)
    # Name nodes already reported as disallowed calls; skipped by the
    # excluded-builtin check so one ``eval(...)`` yields one violation.
    # ``ast.walk`` is breadth-first and always yields a ``Call`` before its
    # ``func`` child, so entries land here before the child is inspected.
    flagged_call_funcs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            denied = sorted(
                {
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                    if alias.name.split(".", 1)[0] not in ALLOWED_IMPORT_ROOTS
                }
            )
            if denied:
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": (
                            f"imports not allowed: {denied}; VibeScript source may "
                            f"only import {sorted(ALLOWED_IMPORT_ROOTS)}."
                        ),
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": "relative imports are not allowed in VibeScript source.",
                    }
                )
                continue
            root = str(node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": (
                            f"import not allowed: {root}; VibeScript source may "
                            f"only import {sorted(ALLOWED_IMPORT_ROOTS)}."
                        ),
                    }
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DISALLOWED_CALLS:
                flagged_call_funcs.add(id(node.func))
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": (
                            f"call not allowed: {node.func.id}; use the vibescript_api "
                            "helpers instead of dynamic or filesystem builtins."
                        ),
                    }
                )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": f"dunder access not allowed: {node.attr}",
                    }
                )
            elif node.attr in _DISALLOWED_DOCUMENT_METHODS:
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": (
                            f"document lifecycle call not allowed: {node.attr}; "
                            "VibeScript owns one isolated temporary document."
                        ),
                    }
                )
        elif isinstance(node, ast.Name):
            if node.id == "__builtins__":
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": (
                            "access to __builtins__ is not allowed in "
                            "VibeScript source."
                        ),
                    }
                )
            elif (
                isinstance(node.ctx, ast.Load)
                and node.id in _EXCLUDED_BUILTIN_NAMES
                and node.id not in bound_names
                and id(node) not in flagged_call_funcs
            ):
                violations.append(
                    {
                        "line": node.lineno,
                        "reason": (
                            f"builtin not available in the VibeScript sandbox: "
                            f"{node.id}; allowed builtins are listed by "
                            "vibescript.describe_api."
                        ),
                    }
                )
    if violations:
        raise VibeScriptFailure(
            _failure(
                "SOURCE_POLICY_VIOLATION",
                "source_validation",
                "VibeScript source violates the isolated worker policy.",
                observed={"violations": violations[:20]},
                required_changes=[{"remove_policy_violations": violations[:20]}],
            )
        )


# ---------------------------------------------------------------------------
# Revisions and JSON helpers
# ---------------------------------------------------------------------------


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def source_revision(
    source: str,
    parameters: dict[str, Any],
    expected_outputs: list[str],
) -> str:
    payload = {
        "schema": MODEL_SCHEMA,
        "runtime_version": VIBESCRIPT_VERSION,
        "source": source,
        "parameters": parameters,
        "expected_outputs": expected_outputs,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VibeScriptFailure(
            _failure(
                f"INVALID_{label.upper()}", "schema", f"{label} must be an object."
            )
        )
    try:
        decoded = json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise VibeScriptFailure(
            _failure(
                f"INVALID_{label.upper()}",
                "schema",
                f"{label} is not JSON-safe: {exc}",
            )
        ) from exc
    return decoded


def _clean_parameters(value: Any) -> dict[str, Any]:
    parameters = _json_object(value, "parameters")
    try:
        vibescript_api.Params(**parameters)
    except vibescript_api.VibeScriptError as exc:
        raise VibeScriptFailure(
            _failure(
                "INVALID_PARAMETERS",
                "schema",
                f"parameters must map identifier names to finite numbers: {exc}",
                observed={"parameters": parameters},
            )
        ) from exc
    return parameters


def _clean_outputs(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise VibeScriptFailure(
            _failure(
                "OUTPUTS_REQUIRED",
                "schema",
                "expected_outputs must contain at least one output key.",
            )
        )
    if len(value) > MAX_OUTPUTS:
        raise VibeScriptFailure(
            _failure(
                "TOO_MANY_OUTPUTS",
                "schema",
                f"expected_outputs may contain at most {MAX_OUTPUTS} keys.",
            )
        )
    cleaned = [str(item or "").strip() for item in value]
    if any(not _NAME_PATTERN.fullmatch(item) for item in cleaned):
        raise VibeScriptFailure(
            _failure(
                "INVALID_OUTPUT_NAME",
                "schema",
                "Every output key must start with a letter and contain only "
                "letters, numbers, spaces, dots, underscores, or hyphens.",
                observed={"expected_outputs": cleaned},
            )
        )
    if len(set(cleaned)) != len(cleaned):
        raise VibeScriptFailure(
            _failure(
                "DUPLICATE_OUTPUT_NAME",
                "schema",
                "expected_outputs contains duplicate keys.",
                observed={"expected_outputs": cleaned},
            )
        )
    return cleaned


def _apply_source_edits(source: str, edits: Any) -> str:
    if not isinstance(edits, list) or not edits:
        raise VibeScriptFailure(
            _failure(
                "SOURCE_EDITS_REQUIRED",
                "schema",
                "edits must contain at least one replacement.",
            )
        )
    candidate = source
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise VibeScriptFailure(
                _failure(
                    "INVALID_SOURCE_EDIT",
                    "schema",
                    f"Source edit {index} must be an object.",
                )
            )
        old_text = str(edit.get("old_text") or "")
        new_text = str(edit.get("new_text") or "")
        if not old_text:
            raise VibeScriptFailure(
                _failure(
                    "INVALID_SOURCE_EDIT",
                    "schema",
                    f"Source edit {index} has empty old_text.",
                )
            )
        occurrences = candidate.count(old_text)
        if occurrences != 1:
            raise VibeScriptFailure(
                _failure(
                    "SOURCE_EDIT_NOT_UNIQUE",
                    "source_edit",
                    f"Source edit {index} old_text matched {occurrences} times; "
                    "expected exactly once.",
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


def _apply_parameter_merge_patch(
    parameters: dict[str, Any], raw_patch: Any, label: str
) -> dict[str, Any]:
    """Apply an RFC 7396 merge patch to flat params with schema-stage failures."""
    patch = _json_object(raw_patch, label)
    if not patch:
        raise VibeScriptFailure(
            _failure("EMPTY_PARAMETER_PATCH", "schema", f"{label} cannot be empty.")
        )
    merged = _merge_patch(parameters, patch)
    if not isinstance(merged, dict):
        raise VibeScriptFailure(
            _failure(
                "INVALID_PARAMETER_RESULT",
                "schema",
                "The parameter merge patch must leave params as an object.",
            )
        )
    return _clean_parameters(merged)


# ---------------------------------------------------------------------------
# Document model objects
# ---------------------------------------------------------------------------


def _add_string_property(obj: Any, name: str, group: str = "VibeScript") -> None:
    if name not in list(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty("App::PropertyString", name, group)


def _safe_internal_name(value: str, prefix: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))
    clean = re.sub(r"_+", "_", clean).strip("_")
    if not clean or not clean[0].isalpha():
        clean = f"{prefix}_{clean}" if clean else prefix
    return clean[:80]


def _model_objects(doc: Any) -> list[Any]:
    result: list[Any] = []
    for obj in list(doc.findObjects(Type="App::Part") or []):
        model_id = str(getattr(obj, PROP_MODEL_ID, "") or "")
        properties = set(getattr(obj, "PropertiesList", []) or [])
        if (
            _MODEL_ID_PATTERN.fullmatch(model_id)
            and PROP_SOURCE in properties
            and PROP_REVISION in properties
        ):
            result.append(obj)
    return result


def _find_model(
    doc: Any,
    model_id: str,
    *,
    candidates: list[Any] | None = None,
) -> Any | None:
    clean = str(model_id or "").strip().lower()
    if not clean:
        return None
    if not _MODEL_ID_PATTERN.fullmatch(clean):
        raise VibeScriptFailure(
            _failure(
                "INVALID_MODEL_ID",
                "precondition",
                "model_id must be a 32-character lowercase hexadecimal id.",
                requested={"model_id": model_id},
            )
        )
    live_models = _model_objects(doc) if candidates is None else candidates
    matches = [obj for obj in live_models if getattr(obj, PROP_MODEL_ID) == clean]
    if len(matches) > 1:
        raise VibeScriptFailure(
            _failure(
                "DUPLICATE_MODEL_ID",
                "document_state",
                f"Multiple FreeCAD objects claim VibeScript model id {clean}.",
                observed={"objects": [obj.Name for obj in matches]},
            )
        )
    return matches[0] if matches else None


def _output_objects(container: Any) -> dict[str, Any]:
    published = scripted_publication.model_publications(container)
    if published:
        return published
    result: dict[str, Any] = {}
    for child in list(getattr(container, "Group", []) or []):
        key = str(getattr(child, PROP_OUTPUT_KEY, "") or "")
        if key:
            result[key] = child
    return result


def _model_summary(container: Any, *, include_source: bool) -> dict[str, Any]:
    outputs_raw = str(getattr(container, PROP_OUTPUTS, "{}") or "{}")
    parameters_raw = str(getattr(container, PROP_PARAMETERS, "{}") or "{}")
    interfaces_raw = str(getattr(container, PROP_INTERFACES, "{}") or "{}")
    try:
        outputs = json.loads(outputs_raw)
    except ValueError:
        outputs = {"invalid_json": outputs_raw}
    try:
        parameters = json.loads(parameters_raw)
    except ValueError:
        parameters = {"invalid_json": parameters_raw}
    try:
        interfaces = json.loads(interfaces_raw)
    except ValueError:
        interfaces = {"invalid_json": interfaces_raw}
    summary = {
        "model_id": str(getattr(container, PROP_MODEL_ID, "") or ""),
        "object_name": str(getattr(container, "Name", "") or ""),
        "label": str(getattr(container, "Label", "") or ""),
        "revision": str(getattr(container, PROP_REVISION, "") or ""),
        "runtime_version": str(getattr(container, PROP_RUNTIME_VERSION, "") or ""),
        "parameters": parameters,
        "expected_outputs": list(outputs),
        "outputs": outputs,
        "interfaces": interfaces,
    }
    if include_source:
        summary["source"] = str(getattr(container, PROP_SOURCE, "") or "")
    return summary


def _model_contract(
    container: Any, *, validate_source_text: bool = True
) -> dict[str, Any]:
    try:
        parameters = json.loads(str(getattr(container, PROP_PARAMETERS) or "{}"))
        output_map = json.loads(str(getattr(container, PROP_OUTPUTS) or "{}"))
        interfaces = json.loads(str(getattr(container, PROP_INTERFACES, "{}") or "{}"))
    except (TypeError, ValueError) as exc:
        raise VibeScriptFailure(
            _failure(
                "MODEL_METADATA_INVALID",
                "document_state",
                f"Persisted VibeScript model metadata is invalid: {exc}",
                observed={"model_id": str(getattr(container, PROP_MODEL_ID, "") or "")},
            )
        ) from exc
    if (
        not isinstance(parameters, dict)
        or not isinstance(output_map, dict)
        or not isinstance(interfaces, dict)
    ):
        raise VibeScriptFailure(
            _failure(
                "MODEL_METADATA_INVALID",
                "document_state",
                "Persisted parameters and outputs must both be JSON objects.",
            )
        )
    source = str(getattr(container, PROP_SOURCE, "") or "")
    if validate_source_text:
        validate_source(source)
    return {
        "model_name": str(getattr(container, "Label", "") or ""),
        "source": source,
        "parameters": _json_object(parameters, "parameters"),
        "expected_outputs": _clean_outputs(list(output_map)),
        "interfaces": interfaces,
    }


# ---------------------------------------------------------------------------
# Persisted artifacts
# ---------------------------------------------------------------------------


def _project_root(service: Any) -> Path:
    value = str(service.project_scope_snapshot().get("root") or "").strip()
    if not value:
        raise VibeScriptFailure(
            _failure(
                "PROJECT_ROOT_UNAVAILABLE",
                "precondition",
                "Project root is unavailable.",
            )
        )
    return Path(value)


def _model_directory(project_root: str | Path, model_id: str) -> Path:
    return Path(project_root) / "vibescript" / model_id


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"Persisted VibeScript {label} is invalid: {exc}",
                observed={"path": str(path)},
            )
        ) from exc
    if not isinstance(payload, dict):
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"Persisted VibeScript {label} must be a JSON object.",
                observed={"path": str(path)},
            )
        )
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _artifact_contract(
    project_root: str | Path, model_id: str
) -> dict[str, Any] | None:
    directory = _model_directory(project_root, model_id)
    manifest_path = directory / "manifest.json"
    source_path = directory / "model.py"
    parameters_path = directory / "parameters.json"
    if not directory.is_dir():
        return None
    if (
        not manifest_path.is_file()
        or not source_path.is_file()
        or not parameters_path.is_file()
    ):
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INCOMPLETE",
                "document_state",
                "The persisted VibeScript model is missing its manifest, source, "
                "or parameters.",
                observed={"artifact_directory": str(directory)},
            )
        )
    manifest = _read_json_object(manifest_path, "manifest")
    if (
        manifest.get("schema") != MODEL_SCHEMA
        or str(manifest.get("model_id") or "") != model_id
    ):
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                "The persisted VibeScript manifest identity is invalid.",
                observed={"path": str(manifest_path), "manifest": manifest},
            )
        )
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                f"Persisted VibeScript source could not be read: {exc}",
                observed={"path": str(source_path)},
            )
        ) from exc
    parameters = _read_json_object(parameters_path, "parameters")
    output_map = manifest.get("outputs") or {}
    if not isinstance(output_map, dict):
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                "Persisted VibeScript outputs must be an object.",
                observed={"path": str(manifest_path)},
            )
        )
    output_facts = manifest.get("output_facts") or {}
    if not isinstance(output_facts, dict):
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                "Persisted VibeScript output facts must be an object.",
                observed={"path": str(manifest_path)},
            )
        )
    interfaces = manifest.get("interfaces") or {}
    if not isinstance(interfaces, dict):
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "document_state",
                "Persisted VibeScript interfaces must be an object.",
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
    calculated_revision = source_revision(source, parameters, expected_outputs)
    if working_revision != calculated_revision:
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_REVISION_MISMATCH",
                "document_state",
                "Persisted VibeScript source and metadata do not match the "
                "working revision.",
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
        "expected_outputs": expected_outputs,
        "outputs": output_map,
        "output_facts": output_facts,
        "interfaces": interfaces,
        "working_revision": working_revision,
        "accepted_revision": accepted_revision,
        "state": state,
        "latest_attempt": manifest.get("latest_attempt") or {},
        "directory": directory,
        "manifest": manifest,
    }


def _artifact_summary(
    contract: dict[str, Any], *, include_source: bool
) -> dict[str, Any]:
    summary = {
        "model_id": contract["model_id"],
        "object_name": "",
        "label": contract["model_name"],
        "revision": contract["working_revision"],
        "working_revision": contract["working_revision"],
        "accepted_revision": contract["accepted_revision"],
        "state": contract["state"],
        "parameters": contract["parameters"],
        "expected_outputs": contract["expected_outputs"],
        "outputs": contract["outputs"],
        "interfaces": contract["interfaces"],
    }
    if include_source:
        summary["source"] = contract["source"]
    return summary


def _merge_artifact_model_summaries(
    native_models: list[dict[str, Any]],
    project_root: str | Path | None,
) -> list[dict[str, Any]]:
    """Merge persisted candidates without touching the live FreeCAD document."""

    summaries = {
        item["model_id"]: item
        for item in native_models
        if isinstance(item, dict) and str(item.get("model_id") or "")
    }
    root = Path(project_root) if project_root else None
    artifact_root = root / "vibescript" if root else None
    if artifact_root is not None and artifact_root.is_dir():
        for directory in sorted(artifact_root.iterdir()):
            if not directory.is_dir() or not _MODEL_ID_PATTERN.fullmatch(
                directory.name
            ):
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
    return merge_model_summaries(capture_model_summaries(doc), project_root)


def capture_model_summaries(doc: Any) -> list[dict[str, Any]]:
    """Capture only persisted FreeCAD properties from the live document."""

    return [_model_summary(obj, include_source=False) for obj in _model_objects(doc)]


def merge_model_summaries(
    native_models: list[dict[str, Any]],
    project_root: str | Path | None,
) -> list[dict[str, Any]]:
    """Merge filesystem artifacts into a detached native-model snapshot."""

    return _merge_artifact_model_summaries(native_models, project_root)


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
        "runtime_version": VIBESCRIPT_VERSION,
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
        "runtime_version": VIBESCRIPT_VERSION,
        "expected_outputs": prepared["expected_outputs"],
        "outputs": prepared["accepted_outputs"],
        "output_facts": prepared["accepted_output_facts"],
        "interfaces": prepared["accepted_interfaces"],
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


def _mirror_model(
    prepared: dict[str, Any],
    output_map: dict[str, Any],
    output_facts: dict[str, Any],
    interfaces: dict[str, Any],
) -> dict[str, str]:
    directory = _model_directory(prepared["project_root"], prepared["model_id"])
    directory.mkdir(parents=True, exist_ok=True)
    revision = str(prepared["revision"])
    source = str(prepared["source"])
    parameters = prepared["parameters"]
    revisions_directory = directory / "revisions"
    revision_source_path = revisions_directory / f"{revision}.py"
    revision_parameters_path = revisions_directory / f"{revision}.parameters.json"
    revision_manifest_path = revisions_directory / f"{revision}.manifest.json"
    if (
        revision_source_path.exists()
        and revision_source_path.read_text(encoding="utf-8") != source
    ):
        raise VibeScriptFailure(
            _failure(
                "MODEL_ARTIFACT_INVALID",
                "commit",
                f"VibeScript revision {revision} already exists with different source.",
            )
        )
    manifest = {
        "schema": MODEL_SCHEMA,
        "model_id": prepared["model_id"],
        "label": prepared["model_name"],
        "state": "accepted",
        "revision": revision,
        "working_revision": revision,
        "accepted_revision": revision,
        "runtime_version": VIBESCRIPT_VERSION,
        "expected_outputs": prepared["expected_outputs"],
        "outputs": output_map,
        "output_facts": output_facts,
        "interfaces": interfaces,
        "latest_attempt": {
            "revision": revision,
            "status": "accepted",
            "path": str(Path("attempts") / revision),
        },
    }
    if not revision_source_path.exists():
        _write_text(revision_source_path, source)
        _write_json(revision_parameters_path, parameters)
        _write_json(revision_manifest_path, manifest)
    _write_text(directory / "model.py", source)
    _write_json(directory / "parameters.json", parameters)
    _write_json(directory / "manifest.json", manifest)
    attempt_manifest_path = directory / "attempts" / revision / "manifest.json"
    if attempt_manifest_path.is_file():
        attempt_manifest = _read_json_object(attempt_manifest_path, "attempt manifest")
        attempt_manifest["status"] = "accepted"
        _write_json(attempt_manifest_path, attempt_manifest)
    return {
        "source": str(directory / "model.py"),
        "parameters": str(directory / "parameters.json"),
        "manifest": str(directory / "manifest.json"),
        "revision_source": str(revision_source_path),
    }


def record_failed_attempt(
    prepared: dict[str, Any],
    failure: dict[str, Any],
) -> dict[str, Any]:
    paths = _persist_working_candidate(prepared)
    directory = Path(paths["artifact_directory"])
    attempt = Path(paths["attempt_directory"])
    stored_failure = dict(failure)
    stored_failure.pop("requested", None)
    _write_json(attempt / "failure.json", stored_failure)
    attempt_manifest = _read_json_object(attempt / "manifest.json", "attempt manifest")
    attempt_manifest.update(
        {
            "status": "failed",
            "failure_code": str(failure.get("failure_code") or "VIBESCRIPT_FAILED"),
            "failure_stage": str(failure.get("failure_stage") or "native_call"),
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
    }


def _inspect_latest_attempt(contract: dict[str, Any]) -> dict[str, Any]:
    latest = contract.get("latest_attempt") or {}
    relative = str(latest.get("path") or "").strip()
    if not relative:
        return {}
    directory = (contract["directory"] / relative).resolve()
    if (
        contract["directory"].resolve() not in directory.parents
        or not directory.is_dir()
    ):
        return {
            "status": "invalid_artifact",
            "error": "Latest attempt path is missing or outside the model artifact directory.",
        }
    response = dict(latest)
    response["directory"] = str(directory)
    failure_path = directory / "failure.json"
    if failure_path.is_file():
        response["failure"] = _read_json_object(failure_path, "attempt failure")
    return response


# ---------------------------------------------------------------------------
# Editor integration
# ---------------------------------------------------------------------------


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
        raise VibeScriptFailure(
            _failure(
                "MODEL_NOT_FOUND",
                "precondition",
                f"No VibeScript model has id {model_id!r}.",
            )
        )
    manifest = _read_json_object(manifest_path, "model manifest")
    current_revision = str(
        manifest.get("working_revision") or manifest.get("revision") or ""
    )
    if current_revision != str(expected_revision or ""):
        raise VibeScriptFailure(
            _failure(
                "STALE_MODEL_REVISION",
                "precondition",
                "The VibeScript source changed after the editor loaded it.",
                requested={"expected_revision": expected_revision},
                observed={"current_revision": current_revision},
            )
        )
    validate_source(source)
    parameters = _read_json_object(directory / "parameters.json", "parameters")
    expected_outputs = _clean_outputs(
        manifest.get("expected_outputs") or list((manifest.get("outputs") or {}).keys())
    )
    revision = source_revision(source, parameters, expected_outputs)
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


def revert_artifact_to_accepted(
    project_root: str | Path, model_id: str
) -> dict[str, Any]:
    """Restore accepted source artifacts without touching a live document."""

    project_root = Path(project_root)
    contract = _artifact_contract(project_root, model_id)
    if contract is None:
        raise VibeScriptFailure(
            _failure(
                "MODEL_NOT_FOUND",
                "precondition",
                f"No VibeScript model has id {model_id!r}.",
            )
        )
    accepted = contract["accepted_revision"]
    if not accepted:
        raise VibeScriptFailure(
            _failure(
                "NO_ACCEPTED_REVISION",
                "precondition",
                "This VibeScript model has no accepted revision to restore.",
            )
        )
    directory = contract["directory"]
    source_path = directory / "revisions" / f"{accepted}.py"
    parameters_path = directory / "revisions" / f"{accepted}.parameters.json"
    if not source_path.is_file() or not parameters_path.is_file():
        raise VibeScriptFailure(
            _failure(
                "ACCEPTED_REVISION_MISSING",
                "document_state",
                "The accepted VibeScript revision files are missing.",
            )
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
    return {
        "ok": True,
        "model_id": model_id,
        "working_revision": accepted,
        "source": source,
    }


def revert_working_to_accepted(service: Any, model_id: str) -> dict[str, Any]:
    """Compose artifact revert stages for headless and integration callers."""

    return revert_artifact_to_accepted(_project_root(service), model_id)


def restore_output_display_modes(doc: Any) -> list[str]:
    """Published VibeScript BREP outputs use FreeCAD's default display mode."""
    return []


# ---------------------------------------------------------------------------
# Inspect / delete
# ---------------------------------------------------------------------------


def capture_model_inspection(service: Any, model_id: str) -> dict[str, Any]:
    """Capture one model's live identity and immutable output handles."""

    doc = service._active_document()
    if doc is None:
        raise VibeScriptFailure(
            _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
        )
    live_models = _model_objects(doc)
    container = _find_model(doc, model_id, candidates=live_models)
    output_snapshots = []
    if container is not None:
        output_snapshots = [
            {
                "key": key,
                "object": str(getattr(obj, "Name", "") or ""),
                "_shape": getattr(obj, "Shape", None),
            }
            for key, obj in _output_objects(container).items()
        ]
    return {
        "document_name": str(getattr(doc, "Name", "") or ""),
        "project_root": str(_project_root(service)),
        "model_id": str(model_id or ""),
        "native_model": (
            _model_summary(container, include_source=True)
            if container is not None
            else None
        ),
        "native_models": [
            _model_summary(obj, include_source=False) for obj in live_models
        ],
        "output_snapshots": output_snapshots,
    }


def complete_model_inspection(captured: dict[str, Any]) -> dict[str, Any]:
    """Read artifacts and validate legacy output handles on the provider worker."""

    project_root = Path(str(captured["project_root"]))
    model_id = str(captured["model_id"])
    contract = _artifact_contract(project_root, model_id)
    native_model = captured.get("native_model")
    if native_model is None and contract is None:
        return _failure(
            "MODEL_NOT_FOUND",
            "precondition",
            f"No VibeScript model has id {model_id!r}.",
            observed={
                "available_models": merge_model_summaries(
                    list(captured.get("native_models") or []), project_root
                )
            },
        )
    if contract is None:
        model = dict(native_model)
        model.update(
            {
                "working_revision": model["revision"],
                "accepted_revision": model["revision"],
                "state": "accepted",
            }
        )
    else:
        model = _artifact_summary(contract, include_source=True)
    if native_model is not None:
        model["object_name"] = str(native_model.get("object_name") or "")
        output_geometry: list[dict[str, Any]] = []
        for snapshot in list(captured.get("output_snapshots") or []):
            key = str(snapshot.get("key") or "")
            item = {
                "key": key,
                "object": str(snapshot.get("object") or ""),
            }
            persisted_facts = (
                contract["output_facts"].get(key) if contract is not None else None
            )
            if isinstance(persisted_facts, dict):
                item.update(persisted_facts)
            else:
                # Legacy documents have no exact worker-validated facts yet.
                item["shape"] = vibescript_executor.shape_facts(snapshot.get("_shape"))
            output_geometry.append(item)
        model["accepted_outputs"] = output_geometry
    model["artifact_directory"] = str(_model_directory(project_root, model_id))
    if contract is not None:
        latest_attempt = _inspect_latest_attempt(contract)
        if latest_attempt:
            model["latest_attempt"] = latest_attempt
        accepted_revision = contract["accepted_revision"]
        if accepted_revision and accepted_revision != contract["working_revision"]:
            accepted_path = (
                contract["directory"] / "revisions" / f"{accepted_revision}.py"
            )
            if accepted_path.is_file():
                model["accepted_source"] = accepted_path.read_text(encoding="utf-8")
    return {
        "ok": True,
        "model": model,
        "document": str(captured.get("document_name") or ""),
    }


def inspect_model(service: Any, model_id: str) -> dict[str, Any]:
    """Compose inspection stages for headless and integration callers."""

    try:
        captured = capture_model_inspection(service, model_id)
        return complete_model_inspection(captured)
    except VibeScriptFailure as exc:
        return exc.payload


_ASYNC_DELETE_ARTIFACT_KEY = "_vibecad_async_delete_artifact"


def capture_delete_state(
    service: Any,
    model_id: str,
    expected_revision: str,
    reason: str,
) -> dict[str, Any]:
    """Capture deletion identity without reading or deleting artifacts."""

    doc = service._active_document()
    if doc is None:
        raise VibeScriptFailure(
            _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
        )
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise VibeScriptFailure(
            _failure("DELETE_REASON_REQUIRED", "schema", "reason cannot be empty.")
        )
    live_models = _model_objects(doc)
    container = _find_model(doc, model_id, candidates=live_models)
    return {
        "document_name": str(getattr(doc, "Name", "") or ""),
        "project_root": str(_project_root(service)),
        "model_id": str(model_id or ""),
        "expected_revision": str(expected_revision or "").strip(),
        "reason": clean_reason,
        "native_exists": container is not None,
        "native_revision": (
            str(getattr(container, PROP_REVISION, "") or "")
            if container is not None
            else ""
        ),
        "native_models": [
            _model_summary(obj, include_source=False) for obj in live_models
        ],
    }


def prepare_delete_from_state(captured: dict[str, Any]) -> dict[str, Any]:
    """Validate persisted deletion state on the provider worker."""

    project_root = Path(str(captured["project_root"]))
    model_id = str(captured["model_id"])
    contract = _artifact_contract(project_root, model_id)
    if not captured.get("native_exists") and contract is None:
        return _failure(
            "MODEL_NOT_FOUND",
            "precondition",
            f"No VibeScript model has id {model_id!r}.",
            observed={
                "available_models": merge_model_summaries(
                    list(captured.get("native_models") or []), project_root
                )
            },
        )
    current_revision = (
        contract["working_revision"]
        if contract is not None
        else str(captured.get("native_revision") or "")
    )
    if str(captured.get("expected_revision") or "") != current_revision:
        return _failure(
            "STALE_MODEL_REVISION",
            "precondition",
            "The VibeScript model changed after it was inspected.",
            requested={"expected_revision": captured.get("expected_revision")},
            observed={"current_revision": current_revision},
            required_changes=[{"inspect_model": model_id}],
        )
    artifact_directory = _model_directory(project_root, model_id)
    if not artifact_directory.is_dir():
        return _failure(
            "MODEL_ARTIFACT_MISSING",
            "precondition",
            "The persisted VibeScript artifact directory is missing; deletion "
            "was not started.",
            observed={"artifact_directory": str(artifact_directory)},
        )
    return {
        **captured,
        "current_revision": current_revision,
        "artifact_directory": str(artifact_directory),
    }


def commit_prepared_delete(service: Any, prepared: dict[str, Any]) -> dict[str, Any]:
    """Delete native model ownership in one bounded owner-thread transaction."""

    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "") or "") != prepared.get(
        "document_name"
    ):
        return _failure(
            "DOCUMENT_CHANGED",
            "precondition",
            "The active document changed while VibeScript prepared the deletion.",
        )
    container = _find_model(doc, str(prepared["model_id"]))
    if bool(prepared.get("native_exists")) != (container is not None):
        return _failure(
            "MODEL_CHANGED_DURING_DELETE",
            "precondition",
            "The native VibeScript model presence changed while deletion was prepared.",
        )
    if container is not None and str(getattr(container, PROP_REVISION, "") or "") != str(
        prepared.get("native_revision") or ""
    ):
        return _failure(
            "MODEL_CHANGED_DURING_DELETE",
            "precondition",
            "The native VibeScript model revision changed while deletion was prepared.",
        )
    deleted_objects: list[str] = []
    if container is not None:
        publications = list(_output_objects(container).values())
        internal = [
            container,
            *list(getattr(container, "OutListRecursive", []) or []),
        ]
        dependency_uses = scripted_publication.external_reference_uses(
            doc,
            publications,
            internal_objects=internal,
        )
        if dependency_uses:
            return _failure(
                "MODEL_HAS_DOWNSTREAM_REFERENCES",
                "precondition",
                "The VibeScript model cannot be deleted while assemblies, "
                "drawings, analyses, manufacturing jobs, or other document "
                "objects still reference its published outputs.",
                observed={
                    "references": scripted_publication.json_reference_uses(
                        dependency_uses
                    )
                },
                required_changes=[
                    {"remove_or_retarget_downstream_references": True}
                ],
            )
        original_undo_mode = getattr(doc, "UndoMode", None)
        enabled_undo = isinstance(original_undo_mode, int) and original_undo_mode == 0
        if enabled_undo:
            doc.UndoMode = 1
        transaction_label = "Delete VibeScript model"
        doc.openTransaction(transaction_label)
        try:
            deleted_objects = delete_owned_model_objects(
                doc, PROP_MODEL_ID, str(prepared["model_id"])
            )
            remaining = sorted(
                {name for name in deleted_objects if doc.getObject(name) is not None}
                | {
                    str(obj.Name)
                    for obj in owned_model_objects(
                        doc, PROP_MODEL_ID, str(prepared["model_id"])
                    )
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
            if enabled_undo:
                doc.UndoMode = original_undo_mode
            return _failure(
                "DELETE_FAILED",
                "commit",
                f"VibeScript model deletion failed: {exc}",
            )
    else:
        original_undo_mode = getattr(doc, "UndoMode", None)
        enabled_undo = False
        transaction_label = ""
    payload = {
        "ok": True,
        "deleted_model_id": str(prepared["model_id"]),
        "deleted_revision": str(prepared["current_revision"]),
        "reason": str(prepared["reason"]),
        "deleted_objects": deleted_objects,
        "artifact_directory": str(prepared["artifact_directory"]),
    }
    payload[_ASYNC_DELETE_ARTIFACT_KEY] = {
        "document_name": str(prepared["document_name"]),
        "model_id": str(prepared["model_id"]),
        "artifact_directory": str(prepared["artifact_directory"]),
        "transaction_label": transaction_label,
        "original_undo_mode": original_undo_mode,
        "enabled_undo": enabled_undo,
        "native_deleted": container is not None,
    }
    return payload


def delete_prepared_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    """Atomically retire one artifact tree, then remove its quarantine copy."""

    state = payload.get(_ASYNC_DELETE_ARTIFACT_KEY)
    if not isinstance(state, dict):
        return {"ok": False, "error": "No pending VibeScript deletion artifact."}
    directory = Path(str(state["artifact_directory"]))
    quarantine = (
        directory.parent
        / ".deleted"
        / f"{str(state.get('model_id') or directory.name)}-{uuid.uuid4().hex}"
    )
    try:
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        directory.rename(quarantine)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Artifact quarantine rename failed: {exc}",
            "exception_type": type(exc).__name__,
        }
    try:
        shutil.rmtree(quarantine)
    except Exception as exc:
        return {
            "ok": True,
            "cleanup_warning": str(exc),
            "quarantined_artifact_directory": str(quarantine),
        }
    return {"ok": True, "quarantined_artifact_directory": None}


def finish_delete_artifacts(
    service: Any, payload: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Finalize deletion or undo native removal after artifact failure."""

    state = dict(payload.get(_ASYNC_DELETE_ARTIFACT_KEY) or {})
    doc = service._active_document()
    failure = not isinstance(result, dict) or result.get("ok") is not True
    rollback_error: str | None = None
    if failure and state.get("native_deleted"):
        if doc is None or str(getattr(doc, "Name", "") or "") != state.get(
            "document_name"
        ):
            rollback_error = "The active document changed before deletion rollback."
        else:
            try:
                undo_names = list(getattr(doc, "UndoNames", []) or [])
                expected = str(state.get("transaction_label") or "")
                if not undo_names or str(undo_names[0]) != expected:
                    rollback_error = (
                        "A newer document transaction prevented safe deletion rollback."
                    )
                else:
                    doc.undo()
            except Exception as exc:
                rollback_error = str(exc)
    if doc is not None and state.get("enabled_undo"):
        doc.UndoMode = state.get("original_undo_mode")
    if failure:
        error = str((result or {}).get("error") or "Artifact deletion failed.")
        observed = {
            "artifact_directory": state.get("artifact_directory"),
            "native_rolled_back": bool(state.get("native_deleted"))
            and rollback_error is None,
            "exception_type": (result or {}).get("exception_type"),
        }
        if rollback_error:
            observed["rollback_error"] = rollback_error
            error = f"{error} Rollback failure: {rollback_error}"
        return _failure(
            "VIBESCRIPT_ARTIFACT_DELETE_FAILED",
            "commit",
            error,
            observed=observed,
        )
    payload.pop(_ASYNC_DELETE_ARTIFACT_KEY, None)
    if result.get("cleanup_warning"):
        payload["artifact_cleanup_warning"] = {
            "error": str(result["cleanup_warning"]),
            "quarantined_artifact_directory": result.get(
                "quarantined_artifact_directory"
            ),
        }
    return payload


def delete_model(
    service: Any,
    model_id: str,
    expected_revision: str,
    reason: str,
) -> dict[str, Any]:
    """Compose deletion stages for headless and integration callers."""

    try:
        captured = capture_delete_state(service, model_id, expected_revision, reason)
        prepared = prepare_delete_from_state(captured)
        if prepared.get("ok") is False:
            return prepared
        payload = commit_prepared_delete(service, prepared)
        if payload.get("ok") is not True:
            return payload
        artifact_result = delete_prepared_artifacts(payload)
        return finish_delete_artifacts(service, payload, artifact_result)
    except VibeScriptFailure as exc:
        return exc.payload


# ---------------------------------------------------------------------------
# Prepare / execute (isolated sidecar lifecycle)
# ---------------------------------------------------------------------------


class _ImportedOutput:
    """Detached shape carrier used only during bounded live-document publication."""

    __slots__ = ("Name", "Label", "TypeId", "Shape", "Placement")

    def __init__(
        self,
        *,
        name: str,
        label: str,
        type_id: str,
        shape: Any,
        placement: Any,
    ) -> None:
        self.Name = name
        self.Label = label
        self.TypeId = type_id
        self.Shape = shape
        self.Placement = placement

    def getGlobalPlacement(self) -> Any:
        return self.Placement


class _DetachedSelectionDocument:
    """Read-only object lookup for provider-thread interface resolution."""

    def __init__(self, objects: list[_ImportedOutput]) -> None:
        self.Objects = list(objects)
        self._by_name = {obj.Name: obj for obj in self.Objects}

    def getObject(self, name: str) -> _ImportedOutput | None:
        return self._by_name.get(str(name or ""))


class _DetachedSelectionService:
    """Minimal service surface used to query detached imported BREP shapes."""

    def __init__(self, objects: list[_ImportedOutput]) -> None:
        self._document = _DetachedSelectionDocument(objects)

    def _active_document(self) -> _DetachedSelectionDocument:
        return self._document

    @staticmethod
    def _document_object_summary(obj: _ImportedOutput) -> dict[str, str]:
        return {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
        }


def _freecad_home_path() -> str:
    import FreeCAD as App

    return str(App.getHomePath())


def _freecadcmd_executable(freecad_home: str) -> Path:
    bin_root = Path(str(freecad_home or "")) / "bin"
    names = (
        ("FreeCADCmd.exe", "freecadcmd.exe")
        if sys.platform == "win32"
        else ("FreeCADCmd", "freecadcmd")
    )
    for name in names:
        candidate = bin_root / name
        if candidate.is_file():
            return candidate
    raise VibeScriptFailure(
        _failure(
            "FREECADCMD_MISSING",
            "precondition",
            f"The isolated VibeScript runtime is missing from {bin_root}.",
        )
    )


def _scripted_budgets() -> tuple[float, int]:
    from VibeCADPreferences import load_settings

    settings = load_settings()
    timeout = float(getattr(settings, "scripted_timeout_seconds", 0.0) or 0.0)
    memory_mb = int(getattr(settings, "scripted_memory_limit_mb", 0) or 0)
    if timeout <= 0.0 or memory_mb <= 0:
        raise VibeScriptFailure(
            _failure(
                "INVALID_SCRIPTED_BUDGET",
                "precondition",
                "VibeScript requires positive scripted timeout and memory limits.",
                observed={
                    "scripted_timeout_seconds": timeout,
                    "scripted_memory_limit_mb": memory_mb,
                },
            )
        )
    return timeout, memory_mb * 1024 * 1024


def capture_execution_state(
    service: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Capture live-document metadata without file or geometry work."""

    doc = service._active_document()
    if doc is None:
        raise VibeScriptFailure(
            _failure("NO_DOCUMENT", "precondition", "No active FreeCAD document.")
        )
    operation = str(tool_name or "").strip()
    creating = operation == "vibescript.create_model"
    model_id = "" if creating else str(arguments.get("model_id") or "").strip().lower()
    if not creating and not _MODEL_ID_PATTERN.fullmatch(model_id):
        raise VibeScriptFailure(
            _failure(
                "INVALID_MODEL_ID",
                "precondition",
                "model_id must be a 32-character lowercase hexadecimal id.",
                requested={"model_id": arguments.get("model_id")},
            )
        )
    native_objects = _model_objects(doc)
    matches = [
        obj
        for obj in native_objects
        if str(getattr(obj, PROP_MODEL_ID, "") or "") == model_id
    ]
    if len(matches) > 1:
        raise VibeScriptFailure(
            _failure(
                "DUPLICATE_MODEL_ID",
                "document_state",
                f"Multiple FreeCAD objects claim VibeScript model id {model_id}.",
                observed={"objects": [obj.Name for obj in matches]},
            )
        )
    timeout_seconds, memory_limit_bytes = _scripted_budgets()
    return {
        "document_name": str(getattr(doc, "Name", "") or ""),
        "project_root": str(_project_root(service)),
        "freecad_home": _freecad_home_path(),
        "timeout_seconds": timeout_seconds,
        "memory_limit_bytes": memory_limit_bytes,
        "native_models": [
            _model_summary(obj, include_source=False) for obj in native_objects
        ],
        "native_target": (
            _model_contract(matches[0], validate_source_text=False)
            if matches
            else None
        ),
        "native_target_revision": (
            str(getattr(matches[0], PROP_REVISION, "") or "") if matches else ""
        ),
    }


def prepare_execution_from_state(
    captured: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Prepare source and staging artifacts off the FreeCAD owner thread."""

    project_root = Path(str(captured["project_root"]))
    operation = str(tool_name or "").strip()
    creating = operation == "vibescript.create_model"
    available_models = _merge_artifact_model_summaries(
        list(captured.get("native_models") or []), project_root
    )
    if creating:
        model_name = str(arguments.get("model_name") or "").strip()
        if not _NAME_PATTERN.fullmatch(model_name):
            raise VibeScriptFailure(
                _failure(
                    "INVALID_MODEL_NAME",
                    "schema",
                    "model_name must start with a letter and contain at most 96 "
                    "letters, numbers, spaces, dots, underscores, or hyphens.",
                )
            )
        source = str(arguments.get("source") or "")
        parameters = _clean_parameters(arguments.get("parameters"))
        expected_outputs = _clean_outputs(arguments.get("expected_outputs"))
        validate_source(source)
        duplicates = [
            item
            for item in available_models
            if str(item.get("label") or "") == model_name
        ]
        if duplicates:
            raise VibeScriptFailure(
                _failure(
                    "MODEL_NAME_EXISTS",
                    "precondition",
                    "A VibeScript model with this label already exists; inspect "
                    "and update it by model id instead of creating a duplicate.",
                    observed={"matches": duplicates},
                )
            )
        model_id = uuid.uuid4().hex
        base_revision = ""
        accepted_revision_before = ""
        accepted_outputs: dict[str, Any] = {}
        accepted_output_facts: dict[str, Any] = {}
        accepted_interfaces: dict[str, Any] = {}
    else:
        model_id = str(arguments.get("model_id") or "").strip().lower()
        native_target = captured.get("native_target")
        artifact = _artifact_contract(project_root, model_id)
        if native_target is None and artifact is None:
            raise VibeScriptFailure(
                _failure(
                    "MODEL_NOT_FOUND",
                    "precondition",
                    f"No VibeScript model has id {model_id!r}.",
                    observed={"available_models": available_models},
                )
            )
        if artifact is not None:
            current = {
                "model_name": artifact["model_name"],
                "source": artifact["source"],
                "parameters": artifact["parameters"],
                "expected_outputs": artifact["expected_outputs"],
            }
            base_revision = artifact["working_revision"]
            accepted_revision_before = artifact["accepted_revision"]
            accepted_outputs = artifact["outputs"]
            accepted_output_facts = artifact["output_facts"]
            accepted_interfaces = artifact["interfaces"]
            if accepted_revision_before and native_target is None:
                raise VibeScriptFailure(
                    _failure(
                        "ACCEPTED_MODEL_OBJECT_MISSING",
                        "document_state",
                        "The project records accepted VibeScript geometry, but "
                        "its FreeCAD model object is missing.",
                        observed={
                            "model_id": model_id,
                            "accepted_revision": accepted_revision_before,
                            "artifact_directory": str(artifact["directory"]),
                        },
                    )
                )
            if native_target is not None:
                native_revision = str(captured.get("native_target_revision") or "")
                if native_revision != accepted_revision_before:
                    raise VibeScriptFailure(
                        _failure(
                            "ACCEPTED_REVISION_DIVERGED",
                            "document_state",
                            "The accepted FreeCAD object and project artifact "
                            "have different revisions.",
                            observed={
                                "freecad_revision": native_revision,
                                "artifact_accepted_revision": accepted_revision_before,
                            },
                        )
                    )
        else:
            current = dict(native_target or {})
            validate_source(str(current.get("source") or ""))
            base_revision = str(captured.get("native_target_revision") or "")
            accepted_revision_before = base_revision
            accepted_outputs = dict(
                next(
                    (
                        item.get("outputs")
                        for item in list(captured.get("native_models") or [])
                        if str(item.get("model_id") or "") == model_id
                    ),
                    {},
                )
                or {}
            )
            accepted_output_facts = {}
            accepted_interfaces = dict(current.get("interfaces") or {})
        expected_revision = str(arguments.get("expected_revision") or "").strip()
        if expected_revision != base_revision:
            raise VibeScriptFailure(
                _failure(
                    "STALE_MODEL_REVISION",
                    "precondition",
                    "The VibeScript model changed after it was inspected.",
                    requested={"expected_revision": expected_revision},
                    observed={"current_revision": base_revision},
                    required_changes=[{"inspect_model": model_id}],
                )
            )
        model_name = current["model_name"]
        source = current["source"]
        parameters = current["parameters"]
        expected_outputs = current["expected_outputs"]
        if operation == "vibescript.edit_source":
            source = _apply_source_edits(source, arguments.get("edits"))
            if arguments.get("parameter_patch") is not None:
                parameters = _apply_parameter_merge_patch(
                    parameters, arguments.get("parameter_patch"), "parameter_patch"
                )
        elif operation == "vibescript.set_parameters":
            parameters = _apply_parameter_merge_patch(
                parameters, arguments.get("patch"), "patch"
            )
        elif operation == "vibescript.reconfigure_model":
            source = str(arguments.get("source") or "")
            parameters = _clean_parameters(arguments.get("parameters"))
            expected_outputs = _clean_outputs(arguments.get("expected_outputs"))
        elif operation == "vibescript.editor_rebuild":
            if "source" in arguments:
                source = str(arguments.get("source") or "")
            if "parameters" in arguments:
                parameters = _clean_parameters(arguments.get("parameters"))
            if "expected_outputs" in arguments:
                expected_outputs = _clean_outputs(arguments.get("expected_outputs"))
        else:
            raise VibeScriptFailure(
                _failure(
                    "UNSUPPORTED_VIBESCRIPT_TOOL",
                    "surface",
                    f"Unsupported runner-backed VibeScript tool: {operation}",
                )
            )
        validate_source(source)

    revision = source_revision(source, parameters, expected_outputs)
    if (
        not creating
        and revision == base_revision
        and operation != "vibescript.editor_rebuild"
    ):
        raise VibeScriptFailure(
            _failure(
                "NO_MODEL_CHANGE",
                "precondition",
                "The requested VibeScript edit produces the existing model revision.",
                observed={"revision": revision},
                required_changes=[{"change_source_parameters_or_outputs": True}],
            )
        )

    timeout_seconds = float(captured["timeout_seconds"])
    memory_limit_bytes = int(captured["memory_limit_bytes"])
    prepared = {
        "engine": "vibescript",
        "model_id": model_id,
        "creating": creating,
        "operation": operation,
        "model_name": model_name,
        "source": source,
        "parameters": parameters,
        "expected_outputs": expected_outputs,
        "revision": revision,
        "base_revision": base_revision,
        "accepted_revision_before": accepted_revision_before,
        "accepted_outputs": accepted_outputs,
        "accepted_output_facts": accepted_output_facts,
        "accepted_interfaces": accepted_interfaces,
        "project_root": str(project_root),
        "document_name": str(captured["document_name"]),
        "timeout_seconds": timeout_seconds,
        "memory_limit_bytes": memory_limit_bytes,
    }
    staging = project_root / "vibescript" / ".staging" / uuid.uuid4().hex
    try:
        staging.mkdir(parents=True, exist_ok=False)
        prepared["staging"] = str(staging)
        prepared["freecadcmd_executable"] = str(
            _freecadcmd_executable(str(captured["freecad_home"]))
        )
        prepared["artifacts"] = _persist_working_candidate(prepared)
        _write_json(
            staging / "request.json",
            {
                "schema": "vibecad-vibescript-worker-v1",
                "source": source,
                "parameters": parameters,
                "expected_outputs": expected_outputs,
                "max_operations": DEFAULT_MAX_OPERATIONS,
                "max_seconds": timeout_seconds,
                "memory_limit_bytes": memory_limit_bytes,
                "cpu_limit_seconds": max(1, int(timeout_seconds) + 5),
                "output_limit_bytes": 1024 * 1024 * 1024,
            },
        )
        shutil.copy2(Path(__file__).resolve().parent / "vibescript_worker.py", staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return prepared


def prepare_execution(
    service: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Compose both preparation stages for headless and integration callers."""

    captured = capture_execution_state(service, tool_name, arguments)
    return prepare_execution_from_state(captured, tool_name, arguments)


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
        if isinstance(item, dict) and str(item.get("severity") or "").lower() == "error"
    ]


def _publication_name(model_name: str, output_key: str) -> str:
    return _safe_internal_name(
        f"{model_name}_{output_key}_Published",
        "VibeScriptPublished",
    )


def _parameter_object_name(model_id: str) -> str:
    return f"VibeScriptParams_{model_id[:12]}"


def _external_parameter_expressions(
    root: Any,
    parameter_object: Any,
    parameter_name: str,
) -> list[dict[str, str]]:
    internal = {
        id(root),
        id(parameter_object),
        *(
            id(item)
            for item in scripted_publication.implementation_closure(root)
        ),
    }
    object_name = str(getattr(parameter_object, "Name", "") or "")
    object_label = str(getattr(parameter_object, "Label", "") or "")
    references = {
        f"{object_name}.{parameter_name}",
        f"<<{object_label}>>.{parameter_name}",
    }
    uses: list[dict[str, str]] = []
    # FreeCAD includes ExpressionEngine dependencies in InList, so only actual
    # consumers can contain a reference to this parameter object.  Avoid a
    # document-wide owner-thread scan when retiring a parameter in a large
    # assembly.
    for owner in list(getattr(parameter_object, "InList", []) or []):
        if id(owner) in internal:
            continue
        try:
            expressions = list(getattr(owner, "ExpressionEngine", []) or [])
        except Exception as exc:
            raise scripted_publication.PublicationError(
                f"Could not inspect expressions on "
                f"{getattr(owner, 'Name', '<object>')} before retiring parameter "
                f"{parameter_name!r}.",
                details={
                    "owner": str(getattr(owner, "Name", "") or ""),
                    "parameter_object": object_name,
                    "parameter": parameter_name,
                    "native_error": str(exc),
                },
            ) from exc
        for path, expression in expressions:
            text = str(expression or "")
            if not any(reference in text for reference in references):
                continue
            uses.append(
                {
                    "object": str(getattr(owner, "Name", "") or ""),
                    "property": str(path or ""),
                    "expression": text,
                }
            )
    return uses


def _sync_parameter_object(
    doc: Any,
    root: Any,
    prepared: dict[str, Any],
) -> Any:
    parameter_object = scripted_publication.model_parameter_object(root)
    object_name = _parameter_object_name(prepared["model_id"])
    if parameter_object is None:
        collision = doc.getObject(object_name)
        if collision is not None:
            raise scripted_publication.PublicationError(
                f"The reserved VibeScript parameter object name {object_name!r} "
                "is already used by another document object.",
                details={
                    "object": collision.Name,
                    "type": str(getattr(collision, "TypeId", "") or ""),
                },
            )
        parameter_object = doc.addObject("App::VarSet", object_name)
        root.addObject(parameter_object)
    scripted_publication.tag_object(
        parameter_object,
        role=scripted_publication.ROLE_PARAMETERS,
        engine=PUBLICATION_ENGINE,
        model_id=prepared["model_id"],
        revision=prepared["revision"],
    )
    parameter_object.Label = f"{prepared['model_name']} Parameters"
    scripted_publication.ensure_string_property(
        parameter_object,
        scripted_publication.PROP_PARAMETER_NAMES,
    )
    raw_previous = str(
        getattr(
            parameter_object,
            scripted_publication.PROP_PARAMETER_NAMES,
            "[]",
        )
        or "[]"
    )
    try:
        previous_names = json.loads(raw_previous)
    except ValueError as exc:
        raise scripted_publication.PublicationError(
            "The VibeScript parameter registry is not valid JSON.",
            details={"object": parameter_object.Name, "value": raw_previous},
        ) from exc
    if not isinstance(previous_names, list) or not all(
        isinstance(name, str) for name in previous_names
    ):
        raise scripted_publication.PublicationError(
            "The VibeScript parameter registry must be a list of names.",
            details={"object": parameter_object.Name, "value": previous_names},
        )

    parameters = dict(prepared["parameters"])
    for name in sorted(set(previous_names) - set(parameters)):
        uses = _external_parameter_expressions(
            root,
            parameter_object,
            name,
        )
        if uses:
            raise scripted_publication.PublicationError(
                f"Parameter {name!r} cannot be removed because external "
                "document expressions still use it.",
                details={
                    "parameter_object": parameter_object.Name,
                    "parameter": name,
                    "expressions": uses,
                },
            )
        if name in list(getattr(parameter_object, "PropertiesList", []) or []):
            if not parameter_object.removeProperty(name):
                raise scripted_publication.PublicationError(
                    f"FreeCAD refused to remove retired parameter {name!r}.",
                    details={"parameter_object": parameter_object.Name},
                )

    for name, value in parameters.items():
        if name not in list(getattr(parameter_object, "PropertiesList", []) or []):
            parameter_object.addProperty(
                "App::PropertyFloat",
                name,
                "VibeScript Parameters",
            )
        setattr(parameter_object, name, float(value))
    setattr(
        parameter_object,
        scripted_publication.PROP_PARAMETER_NAMES,
        _canonical_json(sorted(parameters)),
    )
    return parameter_object


def _tag_vibescript_publication(
    published: Any,
    *,
    model_id: str,
    output_key: str,
) -> None:
    _add_string_property(published, PROP_MODEL_ID)
    _add_string_property(published, PROP_OUTPUT_KEY)
    setattr(published, PROP_MODEL_ID, model_id)
    setattr(published, PROP_OUTPUT_KEY, output_key)


def _initialize_publication_root(root: Any, prepared: dict[str, Any]) -> None:
    scripted_publication.tag_object(
        root,
        role=scripted_publication.ROLE_MODEL,
        engine=PUBLICATION_ENGINE,
        model_id=prepared["model_id"],
        revision=prepared["revision"],
    )
    for prop in (
        PROP_MODEL_ID,
        PROP_SOURCE,
        PROP_PARAMETERS,
        PROP_REVISION,
        PROP_RUNTIME_VERSION,
        PROP_OUTPUTS,
        PROP_INTERFACES,
    ):
        _add_string_property(root, prop)
    scripted_publication.ensure_string_property(
        root, scripted_publication.PROP_INTERFACES
    )


def _migrate_legacy_publications(
    doc: Any,
    root: Any,
    prepared: dict[str, Any],
) -> dict[str, Any]:
    """Create stable output objects for pre-publication VibeScript models."""
    grouped_publications = scripted_publication.legacy_grouped_publications(root)
    _initialize_publication_root(root, prepared)
    publications = scripted_publication.model_publications(root)
    legacy_outputs = {
        str(getattr(child, PROP_OUTPUT_KEY, "") or ""): child
        for child in list(getattr(root, "Group", []) or [])
        if not scripted_publication.is_publication(child)
        and scripted_publication.role_of(child)
        not in {
            scripted_publication.ROLE_PARAMETERS,
            scripted_publication.ROLE_PUBLICATION_TARGET,
        }
        and str(getattr(child, PROP_OUTPUT_KEY, "") or "")
    }
    overlap = sorted(set(grouped_publications) & set(legacy_outputs))
    if overlap:
        raise scripted_publication.PublicationError(
            "Legacy scripted outputs claim duplicate output keys.",
            details={"output_keys": overlap},
        )
    migrated_references: list[dict[str, Any]] = []
    owned = [root, *list(getattr(root, "OutListRecursive", []) or [])]
    legacy_sources = {**grouped_publications, **legacy_outputs}
    for key, source in legacy_sources.items():
        if key in publications:
            raise scripted_publication.PublicationError(
                f"Legacy and published outputs both claim key {key!r}.",
                details={
                    "legacy_object": source.Name,
                    "published_object": publications[key].Name,
                },
            )
        published = scripted_publication.create_publication(
            doc,
            root,
            source,
            internal_name=_publication_name(prepared["model_name"], key),
            label=str(getattr(source, "Label", "") or key),
            engine=PUBLICATION_ENGINE,
            model_id=prepared["model_id"],
            output_key=key,
            revision=prepared["accepted_revision_before"],
        )
        _tag_vibescript_publication(
            published,
            model_id=prepared["model_id"],
            output_key=key,
        )
        migrated_references.extend(
            scripted_publication.retarget_references(
                doc,
                source,
                published,
                internal_objects=owned,
            )
        )
        publications[key] = published

    for child in list(getattr(root, "Group", []) or []):
        if (
            scripted_publication.role_of(child)
            in {
                scripted_publication.ROLE_PARAMETERS,
                scripted_publication.ROLE_PUBLICATION_TARGET,
            }
        ):
            continue
        scripted_publication.tag_object(
            child,
            role=scripted_publication.ROLE_IMPLEMENTATION,
            engine=PUBLICATION_ENGINE,
            model_id=prepared["model_id"],
            revision=prepared["accepted_revision_before"],
        )

    implementation = scripted_publication.implementation_closure(root)
    unsafe_uses = scripted_publication.external_reference_uses(
        doc,
        implementation,
        internal_objects=[root, *implementation, *publications.values()],
    )
    if unsafe_uses:
        raise scripted_publication.PublicationError(
            "Downstream objects reference private VibeScript implementation "
            "history. The update was not started because those references "
            "cannot survive regeneration.",
            details={
                "references": scripted_publication.json_reference_uses(unsafe_uses),
                "required_action": (
                    "Retarget downstream objects to the published VibeScript outputs."
                ),
            },
        )
    return {
        "publications": publications,
        "migrated_references": migrated_references,
        "legacy_outputs": sorted(legacy_sources),
    }


def _resolve_published_interfaces(
    service: Any,
    definitions: dict[str, dict[str, Any]],
    publications: dict[str, Any],
) -> dict[str, Any]:
    from tool_impl.service import partdesign_dressup_feature

    resolved: dict[str, Any] = {}
    for name, definition in definitions.items():
        output_key = str(definition.get("output") or "")
        published = publications.get(output_key)
        if published is None:
            raise VibeScriptFailure(
                _failure(
                    "PUBLISHED_INTERFACE_OUTPUT_MISSING",
                    "contract",
                    f"Interface {name!r} refers to missing output {output_key!r}.",
                )
            )
        selection = dict(definition.get("selection") or {})
        if selection.get("type") == "origin":
            selection_result = {
                "mode": "origin",
                "subelements": [],
                "resolved_geometry": [],
            }
        else:
            selection_result = partdesign_dressup_feature.resolve_selection(
                service,
                published,
                selection,
                allow_all_edges=False,
                face_only=False,
            )
            if not selection_result.get("ok"):
                raise VibeScriptFailure(
                    _failure(
                        "PUBLISHED_INTERFACE_UNRESOLVED",
                        "contract",
                        f"Interface {name!r} did not resolve uniquely on "
                        f"published output {output_key!r}.",
                        requested={"interface": name, "definition": definition},
                        observed={"selection_failure": selection_result},
                        required_changes=[
                            {
                                "make_interface_query_unique": name,
                                "expected_count": selection.get("expected_count"),
                            }
                        ],
                    )
                )
        resolved[name] = {
            "output": output_key,
            "selection": selection,
            **(
                {"description": str(definition.get("description") or "")}
                if definition.get("description")
                else {}
            ),
            "resolved": {
                "object": published.Name,
                "subelements": list(selection_result.get("subelements") or []),
                "geometry": list(
                    selection_result.get("resolved_geometry") or []
                ),
            },
        }
    return resolved


def _bind_resolved_interfaces(
    definitions: dict[str, dict[str, Any]],
    publications: dict[str, Any],
) -> dict[str, Any]:
    """Bind provider-thread query results to stable live publication names."""

    bound: dict[str, Any] = {}
    for name, definition in definitions.items():
        output_key = str(definition.get("output") or "")
        published = publications.get(output_key)
        if published is None:
            raise VibeScriptFailure(
                _failure(
                    "PUBLISHED_INTERFACE_OUTPUT_MISSING",
                    "contract",
                    f"Interface {name!r} refers to missing output {output_key!r}.",
                )
            )
        selection = dict(definition.get("selection") or {})
        resolved = dict(definition.get("resolved") or {})
        subelements = list(resolved.get("subelements") or [])
        geometry = list(resolved.get("geometry") or [])
        mode = str(selection.get("type") or "")
        expected = 0 if mode == "origin" else int(selection.get("expected_count") or 0)
        if (
            mode not in {"origin", "query"}
            or len(subelements) != expected
            or len(geometry) != expected
        ):
            raise VibeScriptFailure(
                _failure(
                    "PUBLISHED_INTERFACE_RESULT_INVALID",
                    "contract",
                    f"Detached interface result {name!r} does not match its contract.",
                    observed={
                        "selection": selection,
                        "resolved": resolved,
                        "expected_count": expected,
                    },
                )
            )
        bound[name] = {
            "output": output_key,
            "selection": selection,
            **(
                {"description": str(definition.get("description") or "")}
                if definition.get("description")
                else {}
            ),
            "resolved": {
                "object": published.Name,
                "subelements": subelements,
                "geometry": geometry,
            },
        }
    return bound


def _accept_outputs(
    service: Any,
    doc: Any,
    prepared: dict[str, Any],
    context: dict[str, Any],
    publication_state: dict[str, Any],
) -> None:
    """Publish accepted outputs without replacing their downstream identity."""
    result: dict[str, Any] = context["result"]
    new_objects: list[Any] = context["new_objects"]
    new_names = {str(getattr(obj, "Name", "")) for obj in new_objects}
    foreign = [
        str(key)
        for key, value in result.items()
        if str(getattr(value, "Name", "")) not in new_names
    ]
    if foreign:
        raise VibeScriptFailure(
            _failure(
                "OUTPUT_NOT_CREATED_BY_SCRIPT",
                "contract",
                "Every result output must be an object the script created in "
                f"this run; pre-existing objects were returned for: {foreign}.",
                observed={"foreign_outputs": foreign},
            )
        )

    container = publication_state.get("root")
    created_root = bool(publication_state.get("created_root"))
    if container is None:
        container = doc.addObject(
            "App::Part",
            _safe_internal_name(prepared["model_name"], "VibeScriptModel"),
        )
    _initialize_publication_root(container, prepared)
    publications = scripted_publication.model_publications(container)
    removed_publications: list[str] = []
    removed_keys = sorted(set(publications) - set(result))
    internal = [
        container,
        *scripted_publication.implementation_closure(container),
        *publications.values(),
    ]
    for key in removed_keys:
        published = publications[key]
        uses = scripted_publication.external_reference_uses(
            doc,
            [published],
            internal_objects=internal,
        )
        if uses:
            raise VibeScriptFailure(
                _failure(
                    "PUBLISHED_OUTPUT_IN_USE",
                    "commit",
                    f"VibeScript output {key!r} cannot be removed because "
                    "downstream CAD objects still reference it.",
                    observed={
                        "output_key": key,
                        "published_object": published.Name,
                        "references": scripted_publication.json_reference_uses(uses),
                    },
                    required_changes=[
                        {"remove_or_retarget_downstream_references": published.Name}
                    ],
                )
            )
        removed_publications.extend(
            scripted_publication.delete_publication(doc, container, published)
        )
        del publications[key]

    output_map: dict[str, Any] = {}
    published_outputs: list[dict[str, Any]] = []
    created_publications: list[str] = []
    validated_output_facts = {
        str(item["key"]): dict(item["shape"])
        for item in list(context.get("outputs") or [])
    }
    for raw_key, source in result.items():
        key = str(raw_key)
        if key not in validated_output_facts:
            raise VibeScriptFailure(
                _failure(
                    "OUTPUT_FACTS_MISSING",
                    "commit",
                    f"Validated shape facts are missing for output {key!r}.",
                )
            )
        published = publications.get(key)
        if published is None:
            published = scripted_publication.create_publication(
                doc,
                container,
                source,
                internal_name=_publication_name(prepared["model_name"], key),
                label=str(getattr(source, "Label", "") or key),
                engine=PUBLICATION_ENGINE,
                model_id=prepared["model_id"],
                output_key=key,
                revision=prepared["revision"],
            )
            created_publications.append(published.Name)
            publications[key] = published
        else:
            scripted_publication.update_publication(
                published,
                container,
                source,
                revision=prepared["revision"],
            )
        _tag_vibescript_publication(
            published,
            model_id=prepared["model_id"],
            output_key=key,
        )
        output_map[key] = {
            "object": str(getattr(published, "Name", "") or ""),
        }
        published_outputs.append(
            {
                "key": key,
                "object_name": str(getattr(published, "Name", "") or ""),
                "label": str(getattr(published, "Label", "") or ""),
                "shape": validated_output_facts[key],
            }
        )

    external_candidates = bool(context.get("external_candidates"))
    if external_candidates:
        implementation_root_names: list[str] = []
    else:
        implementation_roots = scripted_publication.group_implementation(
            container,
            new_objects,
            engine=PUBLICATION_ENGINE,
            model_id=prepared["model_id"],
            revision=prepared["revision"],
        )
        implementation_root_names = [obj.Name for obj in implementation_roots]
    resolved_interfaces = _bind_resolved_interfaces(
        dict(context.get("interfaces") or {}),
        publications,
    )
    discarded_implementation = (
        []
        if external_candidates
        else scripted_publication.delete_implementation(doc, container)
    )
    for published in publications.values():
        scripted_publication.clear_implementation_pointer(published)

    container.Label = prepared["model_name"]
    setattr(container, PROP_MODEL_ID, prepared["model_id"])
    setattr(container, PROP_SOURCE, prepared["source"])
    setattr(container, PROP_PARAMETERS, _canonical_json(prepared["parameters"]))
    setattr(container, PROP_REVISION, prepared["revision"])
    setattr(container, PROP_RUNTIME_VERSION, VIBESCRIPT_VERSION)
    setattr(container, PROP_OUTPUTS, _canonical_json(output_map))
    setattr(container, PROP_INTERFACES, _canonical_json(resolved_interfaces))
    setattr(
        container,
        scripted_publication.PROP_INTERFACES,
        _canonical_json(resolved_interfaces),
    )
    setattr(container, scripted_publication.PROP_REVISION, prepared["revision"])

    reference_contracts.validate_removed_interfaces(
        doc,
        list(publications.values()),
        prepared["model_id"],
        set(prepared.get("accepted_interfaces") or {}),
        set(resolved_interfaces),
        preflight=publication_state.get("reference_preflight"),
    )
    reference_refresh = reference_contracts.refresh_after_publication(
        service,
        prepared["model_id"],
        list(publications.values()),
        revision=prepared["revision"],
        preflight=publication_state.get("reference_preflight"),
    )

    output_facts = {
        item["key"]: {"shape": item["shape"]} for item in published_outputs
    }
    context["outputs"] = published_outputs
    context["container"] = container
    context["output_map"] = output_map
    context["diagnostics"] = {
        "async_part_recompute": bool(
            reference_refresh.get("part_recompute_objects")
        ),
        "recompute_deferred": True,
    }
    context["interfaces"] = resolved_interfaces
    context["artifact_request"] = {
        "prepared": {
            key: prepared[key]
            for key in (
                "project_root",
                "model_id",
                "model_name",
                "revision",
                "source",
                "parameters",
                "expected_outputs",
            )
        },
        "output_map": output_map,
        "output_facts": output_facts,
        "interfaces": resolved_interfaces,
    }
    context["publication"] = {
        "created_root": created_root,
        "created_publications": created_publications,
        "removed_publications": removed_publications,
        "implementation_roots": implementation_root_names,
        "discarded_implementation": discarded_implementation,
        "migrated_references": list(
            publication_state.get("migrated_references") or []
        ),
        "reference_refresh": reference_refresh,
    }


def _runner_environment(prepared: dict[str, Any]) -> dict[str, str]:
    staging = Path(str(prepared["staging"]))
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
            "VIBECAD_VIBESCRIPT_REQUEST": str(staging / "request.json"),
            "VIBECAD_VIBESCRIPT_RESULT": str(staging / "result.json"),
            "VIBECAD_VIBESCRIPT_WORKER": str(staging / "vibescript_worker.py"),
        }
    )
    if sys.platform == "win32":
        drive, tail = os.path.splitdrive(str(staging))
        environment["USERPROFILE"] = str(staging)
        if drive:
            environment["HOMEDRIVE"] = drive
            environment["HOMEPATH"] = tail or "\\"
    return environment


def _runner_command(prepared: dict[str, Any]) -> list[str]:
    code = (
        "import os,runpy;"
        "runpy.run_path(os.environ['VIBECAD_VIBESCRIPT_WORKER'],run_name='__main__')"
    )
    return [
        str(prepared["freecadcmd_executable"]),
        "--safe-mode",
        "-c",
        code,
    ]


def _worker_report_failure(report: dict[str, Any], elapsed: float) -> dict[str, Any]:
    exception_kind = str(report.get("exception_kind") or "")
    failure_code = _EXECUTION_FAILURE_CODES.get(
        exception_kind, "VIBESCRIPT_EXECUTION_FAILED"
    )
    stage = "contract" if exception_kind == "contract_violation" else "execution"
    return _failure(
        failure_code,
        stage,
        str(report.get("error") or "VibeScript execution failed."),
        observed={
            "exception_type": report.get("exception_type"),
            "exception_kind": exception_kind or None,
            "traceback": report.get("traceback"),
            "script_frames": report.get("script_frames"),
            "failure_location": report.get("failure_location"),
            "policy_hint": report.get("policy_hint"),
            "stdout": report.get("stdout") or "",
            "transaction": report.get("transaction"),
            "budget": report.get("budget"),
            "feature_report": report.get("feature_report"),
            "elapsed_seconds": elapsed,
        },
        required_changes=[{"correct_source_or_parameters_from_failure_location": True}],
    )


def execute_prepared(
    prepared: dict[str, Any],
    *,
    cancellation_check: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
    memory_limit_bytes: int | None = None,
    max_operations: int | None = None,
) -> dict[str, Any]:
    """Generate and validate a VibeScript candidate outside the GUI process."""
    from VibeCADScriptedProcess import run_process

    staging = Path(str(prepared["staging"]))
    timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else prepared["timeout_seconds"]
    )
    memory = int(
        memory_limit_bytes
        if memory_limit_bytes is not None
        else prepared["memory_limit_bytes"]
    )
    if max_operations is not None:
        request = _read_json_object(staging / "request.json", "worker request")
        request["max_operations"] = int(max_operations)
        _write_json(staging / "request.json", request)
    process = run_process(
        _runner_command(prepared),
        cwd=staging,
        environment=_runner_environment(prepared),
        cancellation_check=cancellation_check,
        timeout_seconds=timeout,
        memory_limit_bytes=memory,
    )
    if not process.get("started"):
        return _failure(
            "RUNNER_START_FAILED",
            "execution",
            f"VibeScript worker could not start: {process.get('error')}",
            observed={
                "exception_type": process.get("exception_type"),
                "freecadcmd_executable": prepared["freecadcmd_executable"],
            },
        )
    if process.get("cancelled"):
        return _failure(
            "RUN_CANCELLED",
            "execution",
            "VibeScript execution was cancelled.",
            observed={"elapsed_seconds": process["elapsed_seconds"]},
            cancelled=True,
        )
    if process.get("memory_exceeded"):
        return _failure(
            "MEMORY_LIMIT_EXCEEDED",
            "execution",
            f"VibeScript exceeded the {memory // (1024 * 1024)} MB memory budget.",
            observed={
                "memory_limit_bytes": memory,
                "observed_memory_bytes": process.get("observed_memory_bytes"),
                "elapsed_seconds": process["elapsed_seconds"],
            },
        )
    if process.get("timed_out"):
        return _failure(
            "EXECUTION_TIMEOUT",
            "execution",
            f"VibeScript execution exceeded {timeout:g} seconds.",
            observed={"elapsed_seconds": process["elapsed_seconds"]},
        )
    result_path = staging / "result.json"
    if not result_path.is_file():
        return _failure(
            "RUNNER_NO_RESULT",
            "execution",
            "VibeScript worker exited without a result.",
            observed={
                "exit_code": process.get("returncode"),
                "stdout": process.get("stdout"),
                "stderr": process.get("stderr"),
                "elapsed_seconds": process["elapsed_seconds"],
            },
        )
    try:
        report = _read_json_object(result_path, "worker result")
    except VibeScriptFailure as exc:
        return exc.payload
    if not report.get("ok"):
        return _worker_report_failure(report, float(process["elapsed_seconds"]))
    report["elapsed_seconds"] = float(process["elapsed_seconds"])
    report["worker_stdout"] = str(process.get("stdout") or "")
    report["worker_stderr"] = str(process.get("stderr") or "")
    return report


def _placement_from_matrix(values: Any) -> Any:
    import FreeCAD as App

    if not isinstance(values, list) or len(values) != 16:
        raise ValueError("placement_matrix must contain exactly 16 numbers.")
    matrix = App.Matrix()
    for name, value in zip(
        (
            "A11", "A12", "A13", "A14",
            "A21", "A22", "A23", "A24",
            "A31", "A32", "A33", "A34",
            "A41", "A42", "A43", "A44",
        ),
        values,
    ):
        setattr(matrix, name, float(value))
    return App.Placement(matrix)


def _compare_brep_transfer(
    key: str, worker: dict[str, Any], native: dict[str, Any]
) -> dict[str, Any]:
    mismatches: dict[str, Any] = {}
    for field in ("valid", "solid_count", "face_count", "edge_count", "vertex_count"):
        if worker.get(field) != native.get(field):
            mismatches[field] = {"worker": worker.get(field), "native": native.get(field)}
    worker_volume = float(worker.get("volume_mm3") or 0.0)
    native_volume = float(native.get("volume_mm3") or 0.0)
    volume_tolerance = max(1.0e-7, abs(worker_volume) * 1.0e-10)
    if abs(worker_volume - native_volume) > volume_tolerance:
        mismatches["volume_mm3"] = {
            "worker": worker_volume,
            "native": native_volume,
            "tolerance": volume_tolerance,
        }
    worker_bounds = dict(worker.get("bounds_mm") or {})
    native_bounds = dict(native.get("bounds_mm") or {})
    for bound in ("min", "max", "size"):
        left = list(worker_bounds.get(bound) or [])
        right = list(native_bounds.get(bound) or [])
        if len(left) != 3 or len(right) != 3 or any(
            abs(float(a) - float(b)) > 1.0e-7 for a, b in zip(left, right)
        ):
            mismatches[f"bounds_mm.{bound}"] = {"worker": left, "native": right}
    if mismatches:
        raise VibeScriptFailure(
            _failure(
                "BREP_TRANSFER_MISMATCH",
                "execution",
                f"Native BREP transfer changed VibeScript output {key!r}.",
                observed={"output": key, "mismatches": mismatches},
            )
        )
    return {"format": "OpenCASCADE BREP", "exact": True}


def import_validated_outputs(
    prepared: dict[str, Any], execution: dict[str, Any]
) -> list[dict[str, Any]]:
    """Load detached BREP outputs; this does not access the live document."""
    import Part

    staging = Path(str(prepared["staging"])).resolve()
    raw = execution.get("outputs")
    if not isinstance(raw, list) or [str(item.get("key") or "") for item in raw] != prepared["expected_outputs"]:
        raise VibeScriptFailure(
            _failure(
                "OUTPUT_MANIFEST_MISMATCH",
                "execution",
                "VibeScript worker outputs do not match the prepared output contract.",
                observed={"outputs": raw, "expected": prepared["expected_outputs"]},
            )
        )
    imported: list[dict[str, Any]] = []
    for item in raw:
        key = str(item["key"])
        path = (staging / str(item.get("brep_path") or "")).resolve()
        if staging not in path.parents or not path.is_file():
            raise VibeScriptFailure(
                _failure(
                    "OUTPUT_FILE_INVALID",
                    "execution",
                    f"VibeScript BREP output for {key!r} is missing or outside staging.",
                )
            )
        shape = Part.Shape()
        shape.importBrep(str(path))
        facts = vibescript_executor.shape_facts(shape)
        if not facts.get("valid") or int(facts.get("solid_count") or 0) < 1:
            raise VibeScriptFailure(
                _failure(
                    "BREP_IMPORT_INVALID",
                    "execution",
                    f"Imported VibeScript output {key!r} is not a valid solid.",
                    observed={"output": key, "shape": facts},
                )
            )
        transfer = _compare_brep_transfer(key, dict(item.get("shape") or {}), facts)
        imported.append(
            {
                "key": key,
                "candidate": _ImportedOutput(
                    name=str(item.get("object_name") or f"Candidate_{key}"),
                    label=str(item.get("label") or key),
                    type_id=str(item.get("type_id") or "Part::Feature"),
                    shape=shape,
                    placement=_placement_from_matrix(item.get("placement_matrix")),
                ),
                "shape": facts,
                "transfer": transfer,
            }
        )
    raw_interfaces = execution.get("interfaces")
    if not isinstance(raw_interfaces, dict):
        raise VibeScriptFailure(
            _failure(
                "OUTPUT_INTERFACE_MANIFEST_INVALID",
                "execution",
                "VibeScript worker interfaces must be an object.",
                observed={"interfaces": raw_interfaces},
            )
        )
    candidates = {item["key"]: item["candidate"] for item in imported}
    execution["resolved_interfaces"] = _resolve_published_interfaces(
        _DetachedSelectionService(list(candidates.values())),
        raw_interfaces,
        candidates,
    )
    return imported


def _legacy_outputs(root: Any) -> list[Any]:
    return [
        child
        for child in list(getattr(root, "Group", []) or [])
        if not scripted_publication.is_publication(child)
        and str(getattr(child, PROP_OUTPUT_KEY, "") or "")
    ]


def _rollback_scope(
    service: Any, doc: Any, root: Any | None
) -> tuple[set[str], dict[str, Any] | None]:
    if root is None:
        return set(), None
    publications = scripted_publication.model_publications(root)
    grouped_publications = (
        scripted_publication.legacy_grouped_publications(root)
        if not publications
        else {}
    )
    legacy_outputs = (
        _legacy_outputs(root) if not publications and not grouped_publications else []
    )
    legacy_targets = list(grouped_publications.values()) or legacy_outputs
    reference_targets = list(publications.values()) or legacy_targets
    preflight = reference_contracts.preflight_regeneration(
        service,
        reference_targets,
        model_root=root if legacy_targets else None,
    )
    preflight["_legacy_targets"] = bool(legacy_targets)
    names = {
        str(getattr(root, "Name", "") or ""),
        *(str(getattr(obj, "Name", "") or "") for obj in root.OutListRecursive),
        *(str(name) for name in list(preflight.get("carrier_objects") or [])),
    }
    return names, preflight


def _restore_after_abort(
    doc: Any,
    before: dict[str, dict[str, Any]],
    shape_names: set[str],
) -> str | None:
    try:
        after = vibescript_executor._rollback_snapshot(
            doc,
            shape_names=shape_names,
            copy_shapes=False,
            detailed_shape_facts=False,
            scoped_metadata=True,
            only_names=shape_names,
        )
        difference = vibescript_executor._rollback_difference(before, after)
        if any(difference.values()):
            return (
                "FreeCAD transaction rollback did not restore the pre-run document "
                f"state: added={difference['added']}, removed={difference['removed']}, "
                f"changed={difference['changed']}."
            )
    except Exception as exc:
        return f"FreeCAD transaction rollback raised: {exc}"
    return None


_ASYNC_COMMIT_KEY = "_vibecad_async_commit"
_ASYNC_ARTIFACT_KEY = "_vibecad_async_artifact"
_ASYNC_REBIND_KEY = "_vibecad_async_rebind"
_ASYNC_VALIDATION_KEY = "_vibecad_async_validation"


def _restore_commit_undo_mode(doc: Any, state: dict[str, Any]) -> None:
    if state.get("enabled_undo"):
        original = state.get("original_undo_mode")
        if getattr(doc, "UndoMode", None) != original:
            doc.UndoMode = original


def _rollback_committed_update(doc: Any, state: dict[str, Any]) -> str | None:
    """Undo publication/rebind transactions and restore recompute side effects."""

    problems: list[str] = []
    labels = list(state.get("transaction_labels") or [])
    try:
        for expected in reversed(labels):
            undo_names = list(getattr(doc, "UndoNames", []) or [])
            if not undo_names or str(undo_names[0]) != expected:
                problems.append(
                    "Cannot safely undo the VibeScript update because a newer "
                    f"document transaction replaced {expected!r}."
                )
                break
            doc.undo()

        before = dict(state.get("rollback_before") or {})
        for name, record in before.items():
            obj = doc.getObject(name)
            shape = record.get("_shape_restore") if isinstance(record, dict) else None
            if obj is None:
                continue
            if (
                shape is not None
                and "Shape" in list(getattr(obj, "PropertiesList", []) or [])
            ):
                obj.Shape = shape
        # Shape restoration touches parent containers and parameter providers.
        # Clear those derived dirty flags only after every shape is back in place.
        for name, record in before.items():
            obj = doc.getObject(name)
            if obj is None:
                continue
            if "Touched" not in list(record.get("state") or []):
                purge = getattr(obj, "purgeTouched", None)
                if callable(purge):
                    purge()
        after = vibescript_executor._rollback_snapshot(
            doc,
            shape_names=set(state.get("shape_scope") or []),
            copy_shapes=False,
            detailed_shape_facts=False,
            scoped_metadata=True,
            only_names=set(state.get("shape_scope") or []),
        )
        difference = vibescript_executor._rollback_difference(before, after)
        if any(difference.values()):
            changed_details = {
                name: {
                    "before": {
                        key: value
                        for key, value in before.get(name, {}).items()
                        if not key.startswith("_")
                    },
                    "after": {
                        key: value
                        for key, value in after.get(name, {}).items()
                        if not key.startswith("_")
                    },
                }
                for name in difference["changed"]
            }
            problems.append(
                "Rollback did not restore the pre-run document state: "
                f"added={difference['added']}, removed={difference['removed']}, "
                f"changed={difference['changed']}, details={changed_details}."
            )
    except Exception as exc:
        problems.append(f"Rollback raised: {exc}")
    finally:
        try:
            _restore_commit_undo_mode(doc, state)
        except Exception as exc:
            problems.append(f"Undo-mode restoration failed: {exc}")
    return " ".join(problems) or None


def _async_commit_failure(
    service: Any,
    payload: dict[str, Any],
    *,
    code: str,
    error: str,
    observed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = dict(
        payload.get(_ASYNC_COMMIT_KEY)
        or payload.get(_ASYNC_REBIND_KEY)
        or payload.get(_ASYNC_VALIDATION_KEY)
        or payload.get(_ASYNC_ARTIFACT_KEY)
        or {}
    )
    doc = service._active_document()
    rollback_error = (
        "The active document changed before rollback."
        if doc is None or str(getattr(doc, "Name", "")) != state.get("document_name")
        else _rollback_committed_update(doc, state)
    )
    details = dict(observed or {})
    details["transaction"] = {
        "opened": True,
        "committed": True,
        "rolled_back": rollback_error is None,
    }
    if rollback_error:
        details["rollback_error"] = rollback_error
        error = f"{error} Rollback failure: {rollback_error}"
    return _failure(code, "native_recompute", error, observed=details)


def persist_commit_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist accepted source metadata on the provider worker."""

    request = payload.get(_ASYNC_ARTIFACT_KEY)
    if not isinstance(request, dict):
        return {"ok": False, "error": "No pending VibeScript artifact request."}
    try:
        mirror = _mirror_model(
            dict(request["prepared"]),
            dict(request["output_map"]),
            dict(request["output_facts"]),
            dict(request["interfaces"]),
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "exception_type": type(exc).__name__,
        }
    return {"ok": True, "mirror": mirror}


def finish_commit_artifacts(
    service: Any,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Finalize artifact persistence or roll back the native publication."""

    if not isinstance(result, dict) or result.get("ok") is not True:
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_ARTIFACT_COMMIT_FAILED",
            error=str((result or {}).get("error") or "Artifact persistence failed."),
            observed={
                "exception_type": (result or {}).get("exception_type"),
            },
        )
    state = dict(payload.get(_ASYNC_VALIDATION_KEY) or {})
    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "")) != state.get("document_name"):
        return _async_commit_failure(
            service,
            payload,
            code="DOCUMENT_CHANGED_DURING_COMMIT",
            error="The active document changed while VibeScript persisted artifacts.",
        )
    current_undo_names = list(getattr(doc, "UndoNames", []) or [])
    if current_undo_names != list(state.get("validation_undo_names") or []):
        return _async_commit_failure(
            service,
            payload,
            code="DOCUMENT_CHANGED_DURING_COMMIT",
            error="The document changed while VibeScript persisted artifacts.",
            observed={
                "validation_undo_names": state.get("validation_undo_names"),
                "current_undo_names": current_undo_names,
            },
        )
    _restore_commit_undo_mode(doc, state)
    payload.pop(_ASYNC_ARTIFACT_KEY, None)
    payload.pop(_ASYNC_VALIDATION_KEY, None)
    payload["mirror"] = dict(result.get("mirror") or {})
    return payload


def _prepare_async_validation(service: Any, payload: dict[str, Any]) -> dict[str, Any]:
    state = dict(payload.get(_ASYNC_COMMIT_KEY) or {})
    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "")) != state.get("document_name"):
        return _async_commit_failure(
            service,
            payload,
            code="DOCUMENT_CHANGED_DURING_COMMIT",
            error="The active document changed during asynchronous Part recompute.",
        )
    carriers = [
        doc.getObject(name) for name in list(state.get("part_objects") or [])
    ]
    missing = [
        name
        for name, carrier in zip(list(state.get("part_objects") or []), carriers)
        if carrier is None
    ]
    if missing:
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_PART_OBJECT_REMOVED",
            error="Native Part carriers disappeared before validation.",
            observed={"missing_objects": missing},
        )
    state["validation_snapshots"] = reference_contracts.capture_native_part_carriers(
        [obj for obj in carriers if obj is not None]
    )
    state["validation_undo_names"] = list(getattr(doc, "UndoNames", []) or [])
    payload.pop(_ASYNC_COMMIT_KEY, None)
    payload[_ASYNC_VALIDATION_KEY] = state
    return payload


def validate_commit(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate recomputed Part shapes on the provider worker."""

    state = dict(payload.get(_ASYNC_VALIDATION_KEY) or {})
    try:
        validation = reference_contracts.validate_native_part_refresh(
            list(state.get("validation_snapshots") or []),
            list(state.get("native_part_expectations") or []),
        )
    except reference_contracts.ReferenceContractError as exc:
        return {"ok": False, "error": str(exc), "observed": exc.details}
    return {"ok": True, "validation": validation}


def finish_commit_validation(
    service: Any,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Finalize an off-thread Part validation on the document owner thread."""

    state = dict(payload.get(_ASYNC_VALIDATION_KEY) or {})
    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "")) != state.get("document_name"):
        return _async_commit_failure(
            service,
            payload,
            code="DOCUMENT_CHANGED_DURING_COMMIT",
            error="The active document changed during native Part validation.",
        )
    current_undo_names = list(getattr(doc, "UndoNames", []) or [])
    if current_undo_names != list(state.get("validation_undo_names") or []):
        return _async_commit_failure(
            service,
            payload,
            code="DOCUMENT_CHANGED_DURING_COMMIT",
            error="The document changed during native Part validation.",
            observed={
                "validation_undo_names": state.get("validation_undo_names"),
                "current_undo_names": current_undo_names,
            },
        )
    if not isinstance(result, dict) or result.get("ok") is not True:
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_PART_RECOMPUTE_FAILED",
            error=str((result or {}).get("error") or "Native Part validation failed."),
            observed=dict((result or {}).get("observed") or {}),
        )
    validation = result.get("validation")
    if not isinstance(validation, dict):
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_PART_VALIDATION_PROTOCOL_ERROR",
            error="Native Part validation returned no structured result.",
        )
    refresh = payload["publication"]["reference_refresh"]
    refresh["native_part_validation"] = validation
    refresh["asynchronous_recompute"] = {
        "completed_objects": list(state.get("completed") or []),
        "ui_thread_blocked": False,
    }
    payload["native_diagnostics"] = service.recompute_diagnostics()
    return payload


def continue_commit(service: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Advance one non-blocking native Part recompute step on the document thread."""

    state = payload.get(_ASYNC_COMMIT_KEY)
    if not isinstance(state, dict):
        return payload
    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "")) != state.get("document_name"):
        return _async_commit_failure(
            service,
            payload,
            code="DOCUMENT_CHANGED_DURING_COMMIT",
            error="The active document changed during asynchronous Part recompute.",
        )
    if bool(getattr(doc, "RecomputePending", False)) or bool(
        getattr(doc, "Recomputing", False)
    ):
        return payload

    current_name = str(state.get("current_object") or "")
    if current_name:
        current = doc.getObject(current_name)
        if current is None:
            return _async_commit_failure(
                service,
                payload,
                code="VIBESCRIPT_PART_OBJECT_REMOVED",
                error=f"Native Part carrier {current_name!r} disappeared during recompute.",
            )
        state.setdefault("completed", []).append(current_name)
        state["current_object"] = ""

    names = list(state.get("part_objects") or [])
    index = int(state.get("next_index") or 0)
    if index >= len(names):
        return _prepare_async_validation(service, payload)
    name = names[index]
    obj = doc.getObject(name)
    if obj is None:
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_PART_OBJECT_REMOVED",
            error=f"Native Part carrier {name!r} disappeared before recompute.",
        )

    contract = reference_contracts.read_contract(obj)
    if (
        isinstance(contract, dict)
        and str(contract.get("domain") or "") == "part_edge_finish"
        and str(state.get("rebind_ready_object") or "") != name
    ):
        from tool_impl.service import part_fillet

        effective_contract = dict(contract)
        effective_contract["source_revision"] = str(state.get("revision") or "")
        prepared_rebind = part_fillet.prepare_scripted_rebind(
            service, obj, effective_contract
        )
        if not prepared_rebind.get("ok"):
            return _async_commit_failure(
                service,
                payload,
                code="VIBESCRIPT_REFERENCE_REBIND_FAILED",
                error=f"Managed Part references on {name!r} could not be prepared.",
                observed={"rebind": prepared_rebind},
            )
        source = prepared_rebind.pop("source")
        placement = getattr(source, "getGlobalPlacement", lambda: source.Placement)()
        state["rebind_request"] = {
            **prepared_rebind,
            "source_name": str(getattr(source, "Name", "") or ""),
            "source_label": str(getattr(source, "Label", "") or ""),
            "source_type": str(getattr(source, "TypeId", "") or ""),
            "source_shape": getattr(source, "Shape", None),
            "source_placement": placement,
        }
        payload.pop(_ASYNC_COMMIT_KEY, None)
        payload[_ASYNC_REBIND_KEY] = state
        return payload
    state.pop("rebind_ready_object", None)
    try:
        queued = int(doc.recomputeAsync([obj], False))
    except Exception as exc:
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_ASYNC_RECOMPUTE_UNSAFE",
            error=(
                f"Native Part carrier {name!r} cannot be recomputed without "
                f"blocking FreeCAD's UI thread: {exc}"
            ),
            observed={"object": name, "type": str(getattr(obj, "TypeId", "") or "")},
        )
    if queued != 1:
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_ASYNC_RECOMPUTE_NOT_QUEUED",
            error=f"FreeCAD did not queue native Part carrier {name!r} exactly once.",
            observed={"queued_requests": queued},
        )
    state["next_index"] = index + 1
    state["current_object"] = name
    return payload


def resolve_commit_rebind(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve a derived Part edge query on the provider worker."""

    state = dict(payload.get(_ASYNC_REBIND_KEY) or {})
    request = dict(state.get("rebind_request") or {})
    resolved = request.get("resolved_selection")
    if isinstance(resolved, dict):
        return {"ok": True, "selection": resolved}
    source = _ImportedOutput(
        name=str(request.get("source_name") or ""),
        label=str(request.get("source_label") or ""),
        type_id=str(request.get("source_type") or ""),
        shape=request.get("source_shape"),
        placement=request.get("source_placement"),
    )
    from tool_impl.service import partdesign_dressup_feature

    selection = partdesign_dressup_feature.resolve_selection(
        _DetachedSelectionService([source]),
        source,
        request.get("selection"),
        allow_all_edges=False,
        face_only=False,
        edge_only=True,
    )
    return {"ok": bool(selection.get("ok")), "selection": selection}


def finish_commit_rebind(
    service: Any,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Apply one provider-resolved Part edge selection on the owner thread."""

    state = dict(payload.get(_ASYNC_REBIND_KEY) or {})
    request = dict(state.get("rebind_request") or {})
    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "")) != state.get("document_name"):
        return _async_commit_failure(
            service,
            payload,
            code="DOCUMENT_CHANGED_DURING_COMMIT",
            error="The active document changed during native Part reference rebinding.",
        )
    feature = doc.getObject(str(request.get("feature_name") or ""))
    source = doc.getObject(str(request.get("source_name") or ""))
    selection = (result or {}).get("selection")
    if feature is None or source is None or not isinstance(selection, dict):
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_REFERENCE_REBIND_FAILED",
            error="Native Part reference objects or resolved selection disappeared.",
            observed={
                "feature": request.get("feature_name"),
                "source": request.get("source_name"),
                "selection": selection,
            },
        )
    from tool_impl.service import part_fillet

    prepared = {
        "ok": True,
        "feature_name": feature.Name,
        "operation": request.get("operation"),
        "source": source,
        "selection": request.get("selection"),
        "size_mm": request.get("size_mm"),
    }
    label = f"Rebind VibeScript Part reference: {feature.Name}"
    doc.openTransaction(label)
    try:
        rebind = part_fillet.apply_scripted_rebind(feature, prepared, selection)
        if not rebind.get("ok"):
            raise reference_contracts.ReferenceContractError(
                "The managed Part edge selection could not be applied.",
                details={"rebind": rebind},
            )
        doc.commitTransaction()
    except Exception as exc:
        doc.abortTransaction()
        details = (
            exc.details
            if isinstance(exc, reference_contracts.ReferenceContractError)
            else {"exception_type": type(exc).__name__, "native_error": str(exc)}
        )
        return _async_commit_failure(
            service,
            payload,
            code="VIBESCRIPT_REFERENCE_REBIND_FAILED",
            error=f"Managed Part references on {feature.Name!r} could not be rebound: {exc}",
            observed=details,
        )
    state.setdefault("transaction_labels", []).append(label)
    state["rebind_ready_object"] = feature.Name
    state.pop("rebind_request", None)
    payload["publication"]["reference_refresh"].setdefault("rebound", []).append(
        rebind
    )
    payload.pop(_ASYNC_REBIND_KEY, None)
    payload[_ASYNC_COMMIT_KEY] = state
    return payload


def cancel_commit(service: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Roll back a publication whose asynchronous Part refresh was cancelled."""

    return _async_commit_failure(
        service,
        payload,
        code="RUN_CANCELLED",
        error="VibeScript publication was cancelled during native Part recompute.",
        observed={"cancelled": True},
    )


def commit_outputs(
    service: Any,
    prepared: dict[str, Any],
    execution: dict[str, Any],
    imported: list[dict[str, Any]],
) -> dict[str, Any]:
    """Atomically publish isolated outputs into the live FreeCAD document."""
    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "")) != prepared["document_name"]:
        raise VibeScriptFailure(
            _failure(
                "DOCUMENT_CHANGED",
                "precondition",
                "The active document changed while VibeScript was generating geometry.",
                observed={
                    "expected_document": prepared["document_name"],
                    "active_document": getattr(doc, "Name", None),
                },
            )
        )
    if bool(getattr(doc, "Recomputing", False)) or bool(
        getattr(doc, "RecomputePending", False)
    ):
        raise VibeScriptFailure(
            _failure(
                "DOCUMENT_RECOMPUTE_IN_PROGRESS",
                "precondition",
                f"Document {doc.Name} has pending recompute work; outputs were not committed.",
            )
        )
    live_models = _model_objects(doc)
    root = _find_model(doc, prepared["model_id"], candidates=live_models)
    label_conflicts = [
        obj
        for obj in live_models
        if obj is not root
        and str(getattr(obj, "Label", "") or "") == prepared["model_name"]
    ]
    if label_conflicts:
        raise VibeScriptFailure(
            _failure(
                "MODEL_NAME_EXISTS_DURING_EXECUTION",
                "precondition",
                "Another VibeScript model acquired this label while the isolated "
                "worker was running; outputs were not committed.",
                observed={
                    "model_name": prepared["model_name"],
                    "objects": [obj.Name for obj in label_conflicts],
                },
            )
        )
    accepted_revision = str(prepared.get("accepted_revision_before") or "")
    if accepted_revision:
        if root is None:
            raise VibeScriptFailure(
                _failure("MODEL_REMOVED", "precondition", "The VibeScript model was removed while its update ran.")
            )
        current_revision = str(getattr(root, PROP_REVISION, "") or "")
        if current_revision != accepted_revision:
            raise VibeScriptFailure(
                _failure(
                    "MODEL_CHANGED_DURING_EXECUTION",
                    "precondition",
                    "The VibeScript model changed while its isolated worker was running.",
                    requested={"accepted_revision": accepted_revision},
                    observed={"current_revision": current_revision},
                )
            )
    elif root is not None:
        raise VibeScriptFailure(
            _failure("MODEL_ID_COLLISION", "precondition", "The generated VibeScript model id is already present.")
        )

    try:
        shape_scope, preflight = _rollback_scope(service, doc, root)
    except reference_contracts.ReferenceContractError as exc:
        return _failure(
            "VIBESCRIPT_REFERENCE_PREFLIGHT_FAILED",
            "precondition",
            str(exc),
            observed={
                **exc.details,
                "transaction": {
                    "opened": False,
                    "committed": False,
                    "aborted": False,
                },
            },
        )
    rollback_before = vibescript_executor._rollback_snapshot(
        doc,
        shape_names=shape_scope,
        copy_shapes=False,
        detailed_shape_facts=False,
        scoped_metadata=True,
        only_names=shape_scope,
    )
    original_undo_mode = getattr(doc, "UndoMode", None)
    enabled_undo = isinstance(original_undo_mode, int) and original_undo_mode == 0
    if enabled_undo:
        doc.UndoMode = 1
    transaction_label = "Accept VibeScript model"
    doc.openTransaction(transaction_label)
    booked = getattr(doc, "getBookedTransactionID", None)
    if callable(booked) and int(booked() or 0) == 0:
        try:
            doc.abortTransaction()
        finally:
            if enabled_undo:
                doc.UndoMode = original_undo_mode
        raise VibeScriptFailure(
            _failure(
                "TRANSACTION_REFUSED",
                "commit",
                "FreeCAD refused the VibeScript publication transaction.",
            )
        )

    publication_state: dict[str, Any] = {}
    removed_objects: list[str] = []
    accepted_context: dict[str, Any] = {}
    try:
        if root is not None:
            publication_state.update(_migrate_legacy_publications(doc, root, prepared))
            publication_state["root"] = root
            publication_state["reference_preflight"] = (
                preflight
                if preflight and not preflight.get("_legacy_targets")
                else reference_contracts.preflight_regeneration(
                    service,
                    list(publication_state["publications"].values()),
                )
            )
            removed_objects.extend(scripted_publication.delete_implementation(doc, root))
        else:
            root = doc.addObject(
                "App::Part",
                _safe_internal_name(prepared["model_name"], "VibeScriptModel"),
            )
            _initialize_publication_root(root, prepared)
            publication_state.update(
                {
                    "root": root,
                    "publications": {},
                    "migrated_references": [],
                    "legacy_outputs": [],
                    "created_root": True,
                }
            )
        publication_state["parameter_object"] = _sync_parameter_object(
            doc, root, prepared
        )
        candidates = {item["key"]: item["candidate"] for item in imported}
        resolved_interfaces = execution.get("resolved_interfaces")
        if not isinstance(resolved_interfaces, dict):
            raise VibeScriptFailure(
                _failure(
                    "OUTPUT_INTERFACE_RESOLUTION_MISSING",
                    "commit",
                    "Detached interface resolution was not completed before publication.",
                )
            )
        context = {
            "result": candidates,
            "new_objects": list(candidates.values()),
            "outputs": [
                {"key": item["key"], "shape": item["shape"]} for item in imported
            ],
            "interfaces": dict(resolved_interfaces),
            "external_candidates": True,
        }
        _accept_outputs(service, doc, prepared, context, publication_state)
        accepted_context.update(context)
        doc.commitTransaction()
    except Exception as exc:
        doc.abortTransaction()
        rollback_error = _restore_after_abort(doc, rollback_before, shape_scope)
        if enabled_undo:
            doc.UndoMode = original_undo_mode
        if isinstance(exc, VibeScriptFailure):
            payload = dict(exc.payload)
        elif isinstance(exc, scripted_publication.PublicationError):
            payload = _failure(
                "VIBESCRIPT_PUBLICATION_FAILED",
                "commit",
                str(exc),
                observed=exc.details,
            )
        elif isinstance(exc, reference_contracts.ReferenceContractError):
            payload = _failure(
                "VIBESCRIPT_REFERENCE_REBIND_FAILED",
                "commit",
                str(exc),
                observed=exc.details,
            )
        else:
            payload = _failure(
                "VIBESCRIPT_COMMIT_FAILED",
                "commit",
                f"VibeScript outputs could not be committed: {exc}",
                observed={"exception_type": type(exc).__name__},
            )
        payload.setdefault("observed", {})["transaction"] = {
            "opened": True,
            "committed": False,
            "aborted": rollback_error is None,
        }
        if rollback_error:
            payload["observed"]["rollback_error"] = rollback_error
            payload["error"] = f"{payload['error']} Rollback failure: {rollback_error}"
        return payload
    container = accepted_context["container"]
    outputs = [
        {
            "key": item["key"],
            "object": (accepted_context["output_map"].get(item["key"]) or {}).get("object"),
            "shape": item["shape"],
            "transfer": item["transfer"],
        }
        for item in imported
    ]
    payload = {
        "ok": True,
        "created": not accepted_revision,
        "updated": bool(accepted_revision),
        "model": _model_summary(container, include_source=False),
        "outputs": outputs,
        "interfaces": accepted_context["interfaces"],
        "removed_objects": removed_objects,
        "created_objects": list(execution.get("created_objects") or []),
        "stdout": str(execution.get("stdout") or ""),
        "publication": accepted_context["publication"],
        "execution": {
            "elapsed_seconds": execution.get("elapsed_seconds"),
            "vibescript_version": VIBESCRIPT_VERSION,
            "budget": execution.get("budget"),
            "isolated": True,
        },
        "native_diagnostics": accepted_context["diagnostics"],
    }
    payload[_ASYNC_ARTIFACT_KEY] = dict(accepted_context["artifact_request"])
    refresh = accepted_context["publication"]["reference_refresh"]
    part_objects = list(refresh.get("part_recompute_objects") or [])
    payload[_ASYNC_COMMIT_KEY] = {
        "document_name": str(getattr(doc, "Name", "") or ""),
        "model_id": prepared["model_id"],
        "revision": prepared["revision"],
        "part_objects": part_objects,
        "native_part_expectations": list(
            refresh.get("native_part_expectations") or []
        ),
        "next_index": 0,
        "current_object": "",
        "completed": [],
        "rollback_before": rollback_before,
        "shape_scope": set(shape_scope),
        "transaction_labels": [transaction_label],
        "original_undo_mode": original_undo_mode,
        "enabled_undo": enabled_undo,
    }
    return continue_commit(service, payload)


def cleanup_prepared(prepared: dict[str, Any]) -> None:
    """Remove isolated worker staging."""
    staging = str(prepared.get("staging") or "").strip()
    if staging:
        shutil.rmtree(staging, ignore_errors=True)
