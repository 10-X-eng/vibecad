# SPDX-License-Identifier: LGPL-2.1-or-later

"""Authoritative VibeCAD tool contracts and runtime registry."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator


class SafetyLevel(str, Enum):
    READ = "read"
    VIEW = "view"
    SAFE_WRITE = "safe_write"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"
    DEVELOPER = "developer"


EDIT_MODE_NONE = "none"
EDIT_MODE_SKETCH = "sketch"
VALID_EDIT_MODES = frozenset({EDIT_MODE_NONE, EDIT_MODE_SKETCH})
FAILURE_STAGES = frozenset(
    {
        "schema",
        "surface",
        "edit_state",
        "precondition",
        "native_call",
        "native_recompute",
        "postcondition",
        "external_process",
    }
)


def unchanged_state() -> dict[str, Any]:
    return {
        "transaction_opened": False,
        "mutation_started": False,
        "commit_attempted": False,
        "commit_succeeded": False,
        "document_changed": False,
        "changed": False,
        "retained": False,
        "created_objects": [],
        "changed_objects": [],
        "deleted_objects": [],
        "repair_targets": [],
    }


def tool_failure(
    tool: str,
    failure_code: str,
    failure_stage: str,
    error: str,
    *,
    requested: Any = None,
    normalized: Any = None,
    observed: Any = None,
    candidates: Any = None,
    allowed_values: Any = None,
    state_change: Mapping[str, Any] | None = None,
    native_diagnostics: Any = None,
    retry_same_call: bool = False,
    required_changes: list[Any] | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Build the single provider-visible contract for a rejected tool call."""
    stage = str(failure_stage or "").strip()
    if stage not in FAILURE_STAGES:
        raise ValueError(f"Unknown VibeCAD tool failure stage: {stage!r}")
    change = unchanged_state()
    if state_change is not None:
        change.update(dict(state_change))
    response: dict[str, Any] = {
        "ok": False,
        "tool": str(tool or "").strip(),
        "failure_code": str(failure_code or "TOOL_EXECUTION_FAILED").strip(),
        "failure_stage": stage,
        "requested": {} if requested is None else requested,
        "normalized": {} if normalized is None else normalized,
        "observed": {} if observed is None else observed,
        "candidates": [] if candidates is None else candidates,
        "allowed_values": [] if allowed_values is None else allowed_values,
        "state_change": change,
        "native_diagnostics": [] if native_diagnostics is None else native_diagnostics,
        "retry": {
            "same_call": bool(retry_same_call),
            "required_changes": list(required_changes or []),
        },
        "error": str(error or "Tool call failed."),
    }
    response.update(details)
    return response


def normalize_tool_failure(
    tool: str,
    requested: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
    *,
    default_stage: str = "native_call",
) -> dict[str, Any]:
    """Enforce the failure contract without interpreting human-readable text."""
    raw = dict(payload)
    stage = str(raw.get("failure_stage") or default_stage)
    if stage not in FAILURE_STAGES:
        stage = default_stage
    document_delta = raw.get("document_delta")
    change = raw.get("state_change")
    if not isinstance(change, Mapping):
        delta = document_delta if isinstance(document_delta, Mapping) else {}
        created = list(delta.get("created_objects") or [])
        changed = list(delta.get("changed_objects") or [])
        deleted = list(delta.get("deleted_objects") or [])
        document_changed = bool(created or changed or deleted)
        change = {
            "transaction_opened": bool(raw.get("transaction_opened")),
            "mutation_started": bool(raw.get("mutation_started") or document_changed),
            "commit_attempted": bool(raw.get("commit_attempted")),
            "commit_succeeded": bool(raw.get("commit_succeeded")),
            "document_changed": document_changed,
            "changed": document_changed,
            "retained": document_changed,
            "created_objects": created,
            "changed_objects": changed,
            "deleted_objects": deleted,
            "repair_targets": list(raw.get("repair_targets") or []),
        }
    retry = raw.get("retry")
    if not isinstance(retry, Mapping):
        retry = {
            "same_call": bool(raw.get("retry_same_call", False)),
            "required_changes": list(raw.get("required_changes") or []),
        }
    native_diagnostics = raw.get("native_diagnostics")
    if native_diagnostics is None:
        native_diagnostics = []
    observed = raw.get("observed", {})
    if not isinstance(observed, Mapping):
        observed = {"raw_observed": observed}
    else:
        observed = dict(observed)
    continuation_keys = (
        "program",
        "source_target",
        "program_id",
        "source_id",
        "working_revision",
        "next_write_expected_revision",
        "failed_candidate",
        "model_state",
    )
    continuation = {key: raw[key] for key in continuation_keys if key in raw}
    reserved_input = {
        "ok",
        "tool",
        "failure_code",
        "failure_stage",
        "error",
        "requested",
        "normalized",
        "observed",
        "candidates",
        "allowed_values",
        "state_change",
        "native_diagnostics",
        "retry",
        "retry_same_call",
        "required_changes",
        "document_delta",
        "transaction_opened",
        "mutation_started",
        "commit_attempted",
        "commit_succeeded",
        "repair_targets",
    }
    reserved_input.update(continuation_keys)
    tool_details = {
        key: value for key, value in raw.items() if key not in reserved_input
    }
    if tool_details:
        observed["tool_details"] = tool_details
    return tool_failure(
        tool,
        str(raw.get("failure_code") or "TOOL_EXECUTION_FAILED"),
        stage,
        str(raw.get("error") or "Tool call failed."),
        requested=raw.get("requested", dict(requested or {})),
        normalized=raw.get("normalized", {}),
        observed=observed,
        candidates=raw.get("candidates", []),
        allowed_values=raw.get("allowed_values", []),
        state_change=change,
        native_diagnostics=native_diagnostics,
        retry_same_call=bool(retry.get("same_call", False)),
        required_changes=list(retry.get("required_changes") or []),
        **continuation,
    )


class ToolArgumentValidationError(ValueError):
    """JSON-schema rejection with the complete provider failure payload."""

    def __init__(self, payload: Mapping[str, Any]):
        self.payload = dict(payload)
        super().__init__(str(self.payload.get("error") or "Invalid tool arguments."))


@dataclass(frozen=True)
class ToolSpec:
    """Single source of truth for one provider-callable operation."""

    name: str
    description: str
    parameters: dict[str, Any]
    safety: SafetyLevel
    workbench: str | None
    contextual: bool
    requires_document: bool
    edit_modes: frozenset[str]
    provider_visible: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ToolSpec":
        name = str(raw.get("name") or "").strip()
        if not name or "." not in name:
            raise ValueError(f"Invalid VibeCAD tool name: {name!r}")
        tool_description = str(raw.get("description") or "").strip()
        if len(tool_description) < 24:
            raise ValueError(
                f"Tool {name} needs a concrete provider description, not a label."
            )
        parameters = deepcopy(raw.get("parameters"))
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError(f"Tool {name} parameters must be a JSON object schema.")
        properties = parameters.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"Tool {name} parameter schema needs properties.")
        for argument_name, argument_schema in properties.items():
            parameter_description = (
                str(argument_schema.get("description") or "").strip()
                if isinstance(argument_schema, Mapping)
                else ""
            )
            if not parameter_description:
                raise ValueError(
                    f"Tool {name} parameter {argument_name!r} needs a direct "
                    "provider description."
                )
        try:
            Draft202012Validator.check_schema(parameters)
        except Exception as exc:
            raise ValueError(f"Tool {name} has an invalid JSON schema: {exc}") from exc
        safety_name = str(raw.get("safety") or "READ").strip().upper()
        try:
            safety = SafetyLevel[safety_name]
        except KeyError as exc:
            raise ValueError(
                f"Tool {name} has unknown safety {safety_name!r}."
            ) from exc
        workbench = str(raw.get("workbench") or "").strip() or None
        contextual = bool(raw.get("contextual", False))
        requires_document = bool(
            raw.get(
                "requires_document",
                bool(workbench)
                or safety in {SafetyLevel.SAFE_WRITE, SafetyLevel.WRITE},
            )
        )
        raw_modes = raw.get("edit_modes")
        if raw_modes is None:
            if safety in {SafetyLevel.READ, SafetyLevel.VIEW}:
                modes = set(VALID_EDIT_MODES)
            elif workbench == "SketcherWorkbench":
                modes = {EDIT_MODE_SKETCH}
            else:
                modes = {EDIT_MODE_NONE}
        else:
            if not isinstance(raw_modes, (list, tuple, set, frozenset)):
                raise ValueError(f"Tool {name} edit_modes must be a list of modes.")
            modes = {str(item).strip() for item in raw_modes}
        unknown_modes = modes - VALID_EDIT_MODES
        if unknown_modes:
            raise ValueError(
                f"Tool {name} has unknown edit modes: {sorted(unknown_modes)}."
            )
        if not modes:
            raise ValueError(f"Tool {name} must allow at least one edit mode.")
        return cls(
            name=name,
            description=tool_description,
            parameters=parameters,
            safety=safety,
            workbench=workbench,
            contextual=contextual,
            requires_document=requires_document,
            edit_modes=frozenset(modes),
            provider_visible=bool(raw.get("provider_visible", True)),
        )

    def supports_edit_mode(self, edit_mode: str) -> bool:
        return str(edit_mode or EDIT_MODE_NONE) in self.edit_modes

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(self.parameters).iter_errors(dict(arguments)),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if not errors:
            return
        error = _most_specific_schema_error(errors[0])
        path = list(error.absolute_path)
        dotted_path = ".".join(str(item) for item in path)
        location = f" at {dotted_path}" if dotted_path else ""
        branch_errors = _schema_branch_errors(errors)
        selected_discriminants = _selected_schema_discriminants(
            self.parameters,
            dict(arguments),
        )
        observed = {
            "path": path,
            "schema_path": list(error.absolute_schema_path),
            "validator": str(error.validator or ""),
            "expected": _schema_json_value(error.validator_value),
            "received": _schema_json_value(error.instance),
            "selected_discriminants": selected_discriminants,
            "branch_errors": branch_errors,
        }
        raise ToolArgumentValidationError(
            tool_failure(
                self.name,
                "SCHEMA_VALIDATION_FAILED",
                "schema",
                f"Invalid arguments for {self.name}{location}: {error.message}",
                requested=dict(arguments),
                observed=observed,
                allowed_values=(
                    list(error.validator_value)
                    if error.validator == "enum"
                    and isinstance(error.validator_value, (list, tuple))
                    else []
                ),
                required_changes=[
                    {
                        "path": path,
                        "validator": str(error.validator or ""),
                        "expected": _schema_json_value(error.validator_value),
                    }
                ],
            )
        )

    def to_schema(self, active_workbench: str | None = None) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": deepcopy(self.parameters),
            "safety": self.safety.value,
            "workbench": self.workbench,
            "active_workbench": active_workbench,
            "requires_document": self.requires_document,
            "edit_modes": sorted(self.edit_modes),
        }


@dataclass(frozen=True)
class VibeCADTool:
    spec: ToolSpec
    handler: Callable[..., Any] | None

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def description(self) -> str:
        return self.spec.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self.spec.parameters

    @property
    def safety(self) -> SafetyLevel:
        return self.spec.safety

    @property
    def workbench(self) -> str | None:
        return self.spec.workbench

    @property
    def contextual(self) -> bool:
        return self.spec.contextual

    def to_schema(self, active_workbench: str | None = None) -> dict[str, Any]:
        return self.spec.to_schema(active_workbench)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, VibeCADTool] = {}

    def register(self, tool: VibeCADTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"VibeCAD tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def register_spec(
        self,
        raw_spec: Mapping[str, Any],
        handler: Callable[..., Any] | None,
    ) -> VibeCADTool:
        tool = VibeCADTool(spec=ToolSpec.from_mapping(raw_spec), handler=handler)
        self.register(tool)
        return tool

    def get(self, name: str) -> VibeCADTool:
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, workbench: str | None = None) -> list[dict[str, Any]]:
        return [
            tool.to_schema(active_workbench=workbench)
            for tool in self._tools.values()
            if tool.spec.provider_visible
        ]

    def call(self, tool_name: str, **kwargs: Any) -> Any:
        tool = self.get(tool_name)
        tool.spec.validate_arguments(kwargs)
        if tool.handler is None:
            raise RuntimeError(
                f"Tool {tool_name} is executed by the provider-session adapter."
            )
        return tool.handler(**kwargs)


def _most_specific_schema_error(error: Any) -> Any:
    leaves: list[Any] = []

    def collect(item: Any) -> None:
        children = _relevant_schema_error_children(item)
        if not children:
            leaves.append(item)
            return
        for child in children:
            collect(child)

    collect(error)
    if not leaves:
        return error
    priority = {
        "enum": 6,
        "type": 5,
        "required": 4,
        "additionalProperties": 3,
        "minimum": 2,
        "exclusiveMinimum": 2,
        "const": 0,
    }
    return max(
        leaves,
        key=lambda item: (
            len(list(getattr(item, "absolute_path", []) or [])),
            priority.get(str(getattr(item, "validator", "")), 1),
            -len(str(getattr(item, "message", ""))),
        ),
    )


def _relevant_schema_error_children(error: Any) -> list[Any]:
    """Keep errors from the discriminated union branch selected by the caller."""
    children = list(getattr(error, "context", []) or [])
    if not children or str(getattr(error, "validator", "")) not in {
        "oneOf",
        "anyOf",
    }:
        return children
    instance = getattr(error, "instance", None)
    branches = getattr(error, "validator_value", None)
    if not isinstance(instance, Mapping) or not isinstance(branches, list):
        return children
    selected: set[int] = set()
    for index, branch in enumerate(branches):
        if not isinstance(branch, Mapping):
            continue
        properties = branch.get("properties")
        if not isinstance(properties, Mapping):
            continue
        constants = {
            str(name): definition.get("const")
            for name, definition in properties.items()
            if isinstance(definition, Mapping) and "const" in definition
        }
        if constants and all(
            instance.get(name) == value for name, value in constants.items()
        ):
            selected.add(index)
    if not selected:
        return children
    filtered = []
    for child in children:
        schema_path = list(getattr(child, "schema_path", []) or [])
        if (
            schema_path
            and isinstance(schema_path[0], int)
            and schema_path[0] in selected
        ):
            filtered.append(child)
    return filtered or children


def _schema_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _schema_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_schema_json_value(item) for item in value]
    return repr(value)


def _schema_branch_errors(errors: list[Any]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []

    def collect(item: Any, branch_path: list[int]) -> None:
        children = _relevant_schema_error_children(item)
        if children:
            for index, child in enumerate(children):
                collect(child, branch_path + [index])
            return
        details.append(
            {
                "branch": branch_path,
                "path": list(getattr(item, "absolute_path", []) or []),
                "validator": str(getattr(item, "validator", "") or ""),
                "expected": _schema_json_value(
                    getattr(item, "validator_value", None)
                ),
                "received": _schema_json_value(getattr(item, "instance", None)),
                "message": str(getattr(item, "message", "") or ""),
            }
        )

    for error_index, item in enumerate(errors):
        collect(item, [error_index])
    return details[:24]


def _selected_schema_discriminants(
    schema: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    selected: dict[str, Any] = {}

    def visit(node: Any, instance: Any, path: list[str]) -> None:
        if not isinstance(node, Mapping):
            return
        branches = node.get("oneOf") or node.get("anyOf")
        if isinstance(branches, list):
            branch_constants: dict[str, set[str]] = {}
            for branch in branches:
                properties = branch.get("properties", {}) if isinstance(branch, Mapping) else {}
                for name, prop in properties.items():
                    if isinstance(prop, Mapping) and "const" in prop:
                        branch_constants.setdefault(str(name), set()).add(
                            repr(prop.get("const"))
                        )
            for name, values in branch_constants.items():
                if len(values) > 1 and isinstance(instance, Mapping) and name in instance:
                    selected[".".join(path + [name])] = _schema_json_value(
                        instance.get(name)
                    )
        properties = node.get("properties")
        if isinstance(properties, Mapping) and isinstance(instance, Mapping):
            for name, child in properties.items():
                if name in instance:
                    visit(child, instance[name], path + [str(name)])
        items = node.get("items")
        if isinstance(items, Mapping) and isinstance(instance, list):
            for index, item in enumerate(instance):
                visit(items, item, path + [str(index)])
        for keyword in ("oneOf", "anyOf", "allOf"):
            for branch in list(node.get(keyword) or []):
                visit(branch, instance, path)

    visit(schema, arguments, [])
    return dict(sorted(selected.items()))
