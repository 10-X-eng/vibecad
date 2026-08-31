# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD provider session orchestration.

The session owns context, tool exposure, execution, steering, cancellation,
and persistence. Product intent stays in the conversation. FreeCAD state stays
in the live state packet. There is no workflow phase machine or prose parser.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
import threading
import time
from typing import Any, Callable

from VibeCADCore import VibeCADService, get_service
from VibeCADProvider import (
    AnthropicProvider,
    BaseProvider,
    CodexProvider,
    GeminiProvider,
    OfflineProvider,
    ProviderUnavailable,
    _model_visible_context,
    provider_input_budget,
    provider_tool_schema_digest,
)
from VibeCADIntentMemoryCompiler import compile_intent_memory_update
from VibeCADModelingSurface import (
    CORE_CONVERSATION_VIEW_TOOLS,
    MODEL_ASSEMBLY_DOMAINS,
    ModelingSurface,
    PROVIDER_READ_TOOL_OWNERS,
    SHARED_CONTEXT_TOOLS,
    infer_engine_from_names,
    is_model_assembly_workbench,
    modeling_surface_from_native_provider,
    provider_engine_from_service,
    resolve_service_surface,
    share_authoring_surface,
    validate_surface_names,
)
from VibeCADTools import (
    SafetyLevel,
    ToolArgumentValidationError,
    normalize_tool_failure,
    tool_failure,
)
from VibeCADNativeOutput import NativeOutputAuthorizer
from VibeCADNativeInput import NativeInputAuthorizer
import VibeCADVibeScriptDomains as vibescript_domains


ProgressCallback = Callable[[dict[str, Any]], None]
CancellationCheck = Callable[[], bool]
SteeringCheck = Callable[[], list[str]]
QuestionCallback = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
DocumentThreadDispatch = Callable[[Callable[[], Any]], Any]

PROVIDER_SAFE_LEVELS = {
    SafetyLevel.READ,
    SafetyLevel.VIEW,
    SafetyLevel.SAFE_WRITE,
}

CORE_PROVIDER_TOOLS = set(CORE_CONVERSATION_VIEW_TOOLS) | set(SHARED_CONTEXT_TOOLS)


VIBESCRIPT_PROVIDER_TOOLS = {
    *CORE_CONVERSATION_VIEW_TOOLS,
    *SHARED_CONTEXT_TOOLS,
    *PROVIDER_READ_TOOL_OWNERS,
    *(
        name
        for pack in vibescript_domains.VIBESCRIPT_WORKBENCH_PACKS.values()
        for name in pack.tool_names
    ),
}


SCRIPTED_ENGINE_PROVIDER_TOOLS = {
    "vibescript": VIBESCRIPT_PROVIDER_TOOLS,
}

MAX_TURN_CONTEXT_JSON_BYTES = 256 * 1024
MAX_RECENT_CONVERSATION_TURNS = 16
MAX_RECENT_CONVERSATION_JSON_BYTES = 48 * 1024
MAX_RECENT_CONVERSATION_TURN_CHARACTERS = 6000
MAX_PROVIDER_TOOL_SCHEMAS_JSON_BYTES = 128 * 1024
# Keep exact schemas intact rather than hiding constraints from the model behind
# an undersized tactical cap. The full-shape query contract still leaves this
# well below the provider wire limit.
# The stable Model+Assembly surface is intentionally complete and remains well
# below the provider's 128 KiB hard bound.  Keep a tighter product budget while
# allowing its exact compact contracts without dynamic schema swapping.
MAX_VIBESCRIPT_TOOL_SCHEMAS_JSON_BYTES = 32 * 1024
VIBESCRIPT_READ_OPERATION_TOOL = "vibescript.read_operation"
VIBESCRIPT_BACKGROUND_SOURCE_TOOLS = frozenset(
    {
        "vibescript.create_assembly",
        "vibescript.create_part",
        "vibescript.create_program",
        "vibescript.build_program",
        "vibescript.edit_source",
        "vibescript.set_inputs",
        "vibescript.reconfigure_program",
        "vibescript.delete_output",
        "vibescript.delete_program",
    }
)
_MAX_RETAINED_VIBESCRIPT_OPERATIONS = 32


class _VibeScriptOperationManager:
    """One process-local lifecycle for long VibeScript source mutations."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._operations: dict[str, dict[str, Any]] = {}
        self._active_operation_id: str | None = None
        self._next_operation_number = 1

    @staticmethod
    def _summary(operation: Mapping[str, Any]) -> dict[str, Any]:
        now = time.monotonic()
        summary = {
            "operation_id": str(operation["operation_id"]),
            "status": str(operation.get("status") or "running"),
            "tool": str(operation["tool"]),
            "target": str(operation.get("target") or ""),
            "elapsed_seconds": round(
                float(operation.get("finished_at") or now)
                - float(operation["started_at"]),
                4,
            ),
        }
        progress = operation.get("progress")
        if isinstance(progress, Mapping) and progress:
            progress_summary = dict(progress)
            if summary["status"] != "running":
                # The terminal tool_call_completed event carries the same full
                # payload returned below as result. Keep only its lifecycle
                # signal here so source, diagnostics, and geometry are emitted
                # exactly once.
                progress_summary.pop("result", None)
            summary["progress"] = progress_summary
        return summary

    @staticmethod
    def _target(tool_name: str, arguments: Mapping[str, Any]) -> str:
        return str(
            arguments.get("program")
            or arguments.get("program_name")
            or arguments.get("output_name")
            or tool_name
        ).strip()

    def active(self) -> dict[str, Any] | None:
        with self._condition:
            operation = (
                self._operations.get(self._active_operation_id)
                if self._active_operation_id is not None
                else None
            )
            return self._summary(operation) if operation is not None else None

    def record_progress(self, operation_id: str, event: Mapping[str, Any]) -> None:
        with self._condition:
            operation = self._operations.get(operation_id)
            if operation is None or operation.get("status") != "running":
                return
            operation["progress"] = dict(event)
            operation["progress_updated_at"] = time.monotonic()
            self._condition.notify_all()

    def _finish(
        self,
        operation_id: str,
        execute: Callable[[str], dict[str, Any]],
    ) -> None:
        try:
            result = execute(operation_id)
            if not isinstance(result, dict):
                result = {
                    "ok": False,
                    "failure_code": "VIBESCRIPT_OPERATION_NO_RESULT",
                    "failure_stage": "operation",
                    "error": "The VibeScript mutation returned no object result.",
                }
        except BaseException as exc:
            result = {
                "ok": False,
                "failure_code": "VIBESCRIPT_OPERATION_EXCEPTION",
                "failure_stage": "operation",
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        with self._condition:
            operation = self._operations.get(operation_id)
            if operation is not None:
                operation.update(
                    {
                        "status": (
                            "succeeded" if bool(result.get("ok")) else "failed"
                        ),
                        "finished_at": time.monotonic(),
                        "result": result,
                    }
                )
            if self._active_operation_id == operation_id:
                self._active_operation_id = None
            removable = [
                key
                for key, value in self._operations.items()
                if value.get("status") != "running"
            ]
            for key in removable[
                : max(
                    0,
                    len(self._operations) - _MAX_RETAINED_VIBESCRIPT_OPERATIONS,
                )
            ]:
                self._operations.pop(key, None)
            self._condition.notify_all()

    def start(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        execute: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._condition:
            if self._active_operation_id is not None:
                active = self._operations.get(self._active_operation_id)
                return {
                    "ok": False,
                    "failure_code": "VIBESCRIPT_OPERATION_ACTIVE",
                    "failure_stage": "precondition",
                    "error": (
                        "Another VibeScript mutation is still running. Read "
                        "that operation before starting a conflicting CAD call."
                    ),
                    "active_operation": (
                        self._summary(active)
                        if active is not None
                        else {"operation_id": self._active_operation_id}
                    ),
                    "next_action": {
                        "tool": VIBESCRIPT_READ_OPERATION_TOOL,
                        "arguments": {
                            "operation_id": self._active_operation_id,
                        },
                    },
                }
            operation_id = f"operation-{self._next_operation_number}"
            self._next_operation_number += 1
            operation = {
                "operation_id": operation_id,
                "status": "running",
                "tool": tool_name,
                "target": self._target(tool_name, arguments),
                "started_at": time.monotonic(),
                "progress": {"event": "queued"},
            }
            self._operations[operation_id] = operation
            self._active_operation_id = operation_id
            response = {
                "ok": True,
                "operation": self._summary(operation),
                "next_action": {
                    "tool": VIBESCRIPT_READ_OPERATION_TOOL,
                    "arguments": {
                        "operation_id": operation_id,
                    },
                },
            }
        threading.Thread(
            target=self._finish,
            args=(operation_id, execute),
            name=f"VibeCAD-VibeScript-{operation_id}",
            daemon=True,
        ).start()
        return response

    def read(self, operation_id: str, wait_seconds: float = 30.0) -> dict[str, Any]:
        with self._condition:
            operation = self._operations.get(operation_id)
            if operation is None:
                return {
                    "ok": False,
                    "failure_code": "VIBESCRIPT_OPERATION_NOT_FOUND",
                    "failure_stage": "precondition",
                    "error": f"Unknown VibeScript operation: {operation_id}.",
                    "known_operation_ids": list(self._operations),
                }
            if operation.get("status") == "running" and wait_seconds > 0:
                progress_stamp = operation.get("progress_updated_at")
                deadline = time.monotonic() + wait_seconds
                while operation.get("status") == "running":
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
                    if operation.get("status") != "running":
                        break
                    if operation.get("progress_updated_at") != progress_stamp:
                        break
            summary = self._summary(operation)
            result = operation.get("result")

        payload: dict[str, Any] = {"ok": True, "operation": summary}
        if summary["status"] == "running":
            payload["next_action"] = {
                "tool": VIBESCRIPT_READ_OPERATION_TOOL,
                "arguments": {
                    "operation_id": operation_id,
                },
            }
        elif isinstance(result, Mapping):
            payload["operation_succeeded"] = bool(result.get("ok"))
            payload["result"] = dict(result)
        return payload


def _vibescript_operation_manager(
    service: VibeCADService,
) -> _VibeScriptOperationManager:
    manager = getattr(service, "_vibecad_vibescript_operations", None)
    if not isinstance(manager, _VibeScriptOperationManager):
        manager = _VibeScriptOperationManager()
        try:
            setattr(service, "_vibecad_vibescript_operations", manager)
        except (AttributeError, TypeError):
            # Lightweight immutable test doubles do not retain cross-runner
            # state; the real VibeCAD service does.
            pass
    return manager


@dataclass(frozen=True)
class VibeCADResponse:
    provider: str
    final_output: str
    context: dict[str, Any]
    tool_trace: list[dict[str, Any]]
    error: str | None = None


def _on_document_thread(
    dispatch: DocumentThreadDispatch | None,
    operation: Callable[[], Any],
) -> Any:
    """Run one FreeCAD/service operation on the owning document thread."""
    if dispatch is None:
        return operation()
    return dispatch(operation)


def _document_recompute_state(service: VibeCADService) -> dict[str, Any]:
    """Read the active document's native recompute state on its owning thread."""
    document = service._active_document()
    return {
        "document": str(getattr(document, "Name", "") or "") or None,
        "recomputing": bool(getattr(document, "Recomputing", False))
        if document is not None
        else False,
        "recompute_pending": bool(getattr(document, "RecomputePending", False))
        if document is not None
        else False,
    }


def _wait_for_document_idle(
    service: VibeCADService,
    dispatch: DocumentThreadDispatch | None,
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    """Wait off-thread until FreeCAD finishes the active native recompute."""
    started = time.monotonic()
    next_progress = started
    while True:
        state = _on_document_thread(
            dispatch,
            lambda: _document_recompute_state(service),
        )
        if not state["recomputing"] and not state["recompute_pending"]:
            state["ok"] = True
            state["waited_seconds"] = round(time.monotonic() - started, 3)
            return state
        if cancellation_check is not None and cancellation_check():
            return {
                "ok": False,
                "cancelled": True,
                "document": state["document"],
                "recomputing": state["recomputing"],
                "recompute_pending": state["recompute_pending"],
                "waited_seconds": round(time.monotonic() - started, 3),
            }
        now = time.monotonic()
        if now >= next_progress:
            _emit(
                progress_callback,
                {
                    "event": "document_recompute_waiting",
                    "document": state["document"],
                    "queued": state["recompute_pending"],
                    "elapsed_seconds": round(now - started, 1),
                },
            )
            next_progress = now + 2.0
        time.sleep(0.05)


def _deferred_publication_recompute(
    service: VibeCADService,
    publication: Mapping[str, Any],
    *,
    dispatch: DocumentThreadDispatch | None,
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    """Refresh exact native downstream carriers without occupying Qt when safe."""

    if publication.get("recompute_deferred") is not True:
        return {"scheduled": False, "mode": "not_requested", "target_count": 0}
    downstream = publication.get("downstream_references")
    if not isinstance(downstream, Mapping):
        return {"scheduled": False, "mode": "no_downstream", "target_count": 0}
    requested_names = list(
        dict.fromkeys(
            str(name).strip()
            for name in list(downstream.get("part_recompute_objects") or [])
            if str(name).strip()
        )
    )
    if not requested_names:
        return {"scheduled": False, "mode": "no_targets", "target_count": 0}

    _emit(
        progress_callback,
        {
            "event": "document_recompute_waiting",
            "phase": "scheduling",
            "target_count": len(requested_names),
        },
    )

    def schedule() -> dict[str, Any]:
        document = service._active_document()
        if document is None:
            raise RuntimeError("The active document closed before downstream recompute.")
        targets = [document.getObject(name) for name in requested_names]
        missing = [
            name for name, target in zip(requested_names, targets) if target is None
        ]
        if missing:
            raise RuntimeError(
                "Downstream recompute targets disappeared: " + ", ".join(missing)
            )
        live_targets = [target for target in targets if target is not None]
        async_recompute = getattr(document, "recomputeAsync", None)
        if callable(async_recompute):
            try:
                queued = int(async_recompute(live_targets, True))
                return {
                    "scheduled": True,
                    "mode": "worker",
                    "target_count": len(live_targets),
                    "request_count": queued,
                    "document": str(document.Name),
                }
            except Exception as exc:
                async_error = str(exc)
        else:
            async_error = "Document.recomputeAsync is unavailable"

        # Preserve correctness for a thread-affine dependency chain. This is
        # intentionally a narrow, exact-target fallback rather than a full
        # document recompute.
        recomputed = int(document.recompute(live_targets, True, True))
        return {
            "scheduled": True,
            "mode": "document_thread_fallback",
            "target_count": len(live_targets),
            "recomputed_object_count": recomputed,
            "async_rejection": async_error,
            "document": str(document.Name),
        }

    started = time.monotonic()
    scheduled = _on_document_thread(dispatch, schedule)
    if scheduled.get("mode") == "worker":
        # Publication has already committed at this point.  A provider-side
        # cancellation must not let the lifecycle accept or reject the
        # candidate while FreeCAD is still mutating its downstream graph.
        # Keep the UI responsive, but wait for the native request to reach a
        # stable document boundary before returning control to the lifecycle.
        idle = _wait_for_document_idle(
            service,
            dispatch,
            None,
            progress_callback,
        )
        scheduled["idle"] = idle
        if not idle.get("ok"):
            scheduled["completed"] = False
            scheduled["elapsed_seconds"] = round(time.monotonic() - started, 4)
            return scheduled

    def inspect() -> dict[str, Any]:
        document = service._active_document()
        if document is None:
            return {"ok": False, "error": "The document closed after recompute."}
        problems = []
        for name in requested_names:
            obj = document.getObject(name)
            if obj is None:
                problems.append({"object": name, "state": ["Missing"]})
                continue
            state = [str(value) for value in list(getattr(obj, "State", []) or [])]
            if any(value in {"Invalid", "Error"} for value in state):
                problems.append({"object": name, "state": state})
        return {"ok": not problems, "problems": problems}

    scheduled["inspection"] = _on_document_thread(dispatch, inspect)
    scheduled["completed"] = bool(scheduled["inspection"].get("ok"))
    scheduled["elapsed_seconds"] = round(time.monotonic() - started, 4)
    _emit(
        progress_callback,
        {
            "event": "vibescript_domain_deferred_recompute_completed",
            "mode": scheduled.get("mode"),
            "target_count": scheduled.get("target_count"),
            "elapsed_seconds": scheduled["elapsed_seconds"],
            "ok": scheduled["completed"],
        },
    )
    return scheduled


def _document_idle_failure(
    tool_name: str,
    requested: dict[str, Any],
    wait_state: dict[str, Any],
) -> dict[str, Any]:
    return tool_failure(
        tool_name,
        "RUN_CANCELLED",
        "precondition",
        "The CAD run was stopped while waiting for FreeCAD to finish recomputing.",
        requested=requested,
        observed={
            "document": wait_state.get("document"),
            "waited_seconds": wait_state.get("waited_seconds", 0.0),
            "recomputing": bool(wait_state.get("recomputing", False)),
            "recompute_pending": bool(wait_state.get("recompute_pending", False)),
        },
    )


def choose_provider(
    service: VibeCADService,
    prefer_online: bool = True,
) -> BaseProvider:
    if not prefer_online:
        return OfflineProvider()
    provider_name = service.provider_name()
    auth = service.auth_state()
    if provider_name != "chatgpt" and not auth.can_call_provider:
        return OfflineProvider()
    if provider_name == "grok":
        from VibeCADGrokAuth import DEFAULT_XAI_API_BASE

        return CodexProvider(
            model=service.provider_model(),
            api_key=service.provider_api_key(),
            auth_mode="api_key",
            reasoning_effort=service.provider_reasoning_effort(),
            base_url=service.provider_base_url() or DEFAULT_XAI_API_BASE,
            web_search_enabled=service.web_search_enabled(),
            skills_enabled=False,
            identity_id="grok",
            identity_label="Grok via X / xAI OAuth",
        )
    if provider_name in {"openai", "chatgpt"}:
        return CodexProvider(
            model=service.provider_model(),
            api_key=(service.provider_api_key() if provider_name == "openai" else None),
            auth_mode="api_key" if provider_name == "openai" else "chatgpt",
            reasoning_effort=service.provider_reasoning_effort(),
            base_url=(
                service.provider_base_url() if provider_name == "openai" else None
            ),
            web_search_enabled=service.web_search_enabled(),
            skills_enabled=service.codex_skills_enabled(),
        )
    if provider_name == "anthropic":
        intent_memory_model = getattr(service, "intent_memory_model", None)
        return AnthropicProvider(
            model=service.provider_model(),
            api_key=service.provider_api_key(),
            reasoning_effort=service.provider_reasoning_effort(),
            base_url=service.provider_base_url(),
            web_search_enabled=service.web_search_enabled(),
            compaction_model=(
                intent_memory_model()
                if callable(intent_memory_model)
                else service.provider_model()
            ),
        )
    if provider_name == "gemini":
        return GeminiProvider(
            model=service.provider_model(),
            api_key=service.provider_api_key(),
            reasoning_effort=service.provider_reasoning_effort(),
            base_url=service.provider_base_url(),
        )
    raise ProviderUnavailable(f"Unsupported provider: {provider_name}")


def provider_execution_identity(provider: BaseProvider) -> dict[str, Any]:
    """Describe the exact provider request without implying an unreported fallback."""

    if isinstance(provider, CodexProvider):
        provider_id = provider.provider_id
        provider_label = provider.provider_label
        fallback_allowed: bool | None = False
    elif isinstance(provider, AnthropicProvider):
        provider_id = "anthropic"
        provider_label = "Anthropic"
        fallback_allowed = False
    elif isinstance(provider, GeminiProvider):
        provider_id = "gemini"
        provider_label = "Google Gemini"
        fallback_allowed = False
    elif isinstance(provider, OfflineProvider):
        provider_id = "offline"
        provider_label = "Offline"
        fallback_allowed = None
    else:
        provider_id = provider.__class__.__name__
        provider_label = provider_id
        fallback_allowed = None

    identity: dict[str, Any] = {
        "provider_id": provider_id,
        "provider_label": provider_label,
        "adapter": provider.__class__.__name__,
    }
    requested_model = str(getattr(provider, "model", "") or "").strip()
    reasoning_effort = str(getattr(provider, "reasoning_effort", "") or "").strip()
    if requested_model:
        identity["requested_model"] = requested_model
        identity["model_selection"] = "explicit"
    elif provider_id not in {"offline"}:
        identity["model_selection"] = "provider_default"
    if reasoning_effort:
        identity["reasoning_effort"] = reasoning_effort
    if fallback_allowed is not None:
        identity["model_fallback_allowed"] = fallback_allowed
    return identity


def _active_document_exists(service: VibeCADService) -> bool:
    return service._active_document() is not None


def _surface_tool_names(
    service: VibeCADService,
    workbench: str | None,
) -> set[str]:
    resolution = resolve_service_surface(service, workbench)
    names = set(resolution.tool_names)
    # Model and Assembly are one stable authoring surface.  Keep its complete
    # contract discoverable while a document opens; individual calls already
    # enforce their exact document and task-state preconditions.
    if not _active_document_exists(service) and not is_model_assembly_workbench(
        workbench
    ):
        names = {
            name
            for name in names
            if service.registry.get(name).safety in {SafetyLevel.READ, SafetyLevel.VIEW}
        }
    if not service.design_review_enabled():
        names.discard("conversation.review_design")
    return names


def _current_edit_mode(service: VibeCADService) -> str:
    return _edit_mode_from_runtime_state(_minimal_runtime_state(service))


def _edit_mode_from_runtime_state(state: dict[str, Any]) -> str:
    if state.get("edit_mode") and _active_sketch_name(state):
        return "sketch"
    return "none"


def _provider_safe_tool_names(
    service: VibeCADService,
    workbench: str | None,
    edit_mode: str,
) -> list[str]:
    """Return live-callable names without serializing provider schemas."""

    result: list[str] = []
    for name in sorted(_surface_tool_names(service, workbench)):
        tool = service.registry.get(name)
        if tool.safety not in PROVIDER_SAFE_LEVELS:
            continue
        # A sketch task is transient, not a different Model/Assembly API.  The
        # runner returns EDIT_STATE_MISMATCH if a call cannot execute until the
        # native task closes, without teaching MCP clients a temporary schema.
        if not is_model_assembly_workbench(
            workbench
        ) and not tool.spec.supports_edit_mode(edit_mode):
            continue
        result.append(name)
    return result


def is_provider_safe_tool(
    service: VibeCADService,
    tool_name: str,
    workbench: str | None = None,
) -> bool:
    try:
        provider_engine = provider_engine_from_service(service)
    except Exception:
        return False
    if provider_engine == "native":
        from VibeCADNativeProviderContext import (
            provider_authorized_native_surface,
            resolve_production_native_surface,
        )

        try:
            registry, surface = resolve_production_native_surface()
        except Exception:
            return False
        state_reader = getattr(service, "native_active_snapshot", None)
        if getattr(surface, "snapshot", None) is not None:
            surface = provider_authorized_native_surface(
                surface,
                state_reader() if callable(state_reader) else None,
                registry=registry,
            )
        clean_name = str(tool_name)
        if not surface.available or clean_name not in surface.tool_names:
            return False
        return True
    try:
        tool = service.registry.get(tool_name)
    except KeyError:
        return False
    active = workbench or service.active_workbench_name()
    if tool.safety not in PROVIDER_SAFE_LEVELS:
        return False
    if tool_name not in _surface_tool_names(service, active):
        return False
    return is_model_assembly_workbench(active) or tool.spec.supports_edit_mode(
        _current_edit_mode(service)
    )


def provider_tool_schemas(
    service: VibeCADService,
    workbench: str | None,
    *,
    runtime_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if provider_engine_from_service(service) == "native":
        from VibeCADNativeProviderContext import native_provider_tool_schemas

        state_reader = getattr(service, "native_active_snapshot", None)
        return (
            native_provider_tool_schemas(state_reader())
            if callable(state_reader)
            else native_provider_tool_schemas()
        )
    state = (
        runtime_state if runtime_state is not None else _minimal_runtime_state(service)
    )
    names = _provider_safe_tool_names(
        service,
        workbench,
        _edit_mode_from_runtime_state(state),
    )
    return [
        _provider_schema_copy(
            service.registry.get(name).to_schema(active_workbench=workbench)
        )
        for name in names
    ]


def _live_provider_surface_state(
    service: VibeCADService,
) -> dict[str, Any]:
    """Capture one coherent authorization snapshot on the document thread."""

    workbench = service.active_workbench_name()
    provider_engine = provider_engine_from_service(service)
    if provider_engine == "native":
        from VibeCADNativeProviderContext import (
            native_active_state,
            provider_authorized_native_surface,
            resolve_production_native_surface,
        )

        registry, provider_surface = resolve_production_native_surface()
        provider_surface = provider_authorized_native_surface(
            provider_surface,
            native_active_state(service) if provider_surface.available else None,
            registry=registry,
        )
        resolution = modeling_surface_from_native_provider(
            workbench,
            provider_surface,
        )
    else:
        resolution = resolve_service_surface(service, workbench)
    runtime_state = _minimal_runtime_state(service)
    return {
        "workbench": workbench,
        "engine": resolution.engine,
        "domain": resolution.domain,
        "surface_id": resolution.surface_id,
        "available": resolution.available,
        "unavailable_reason": resolution.unavailable_reason,
        "runtime_state": runtime_state,
        "tool_names": (
            list(provider_surface.tool_names)
            if provider_engine == "native" and provider_surface.available
            else _provider_safe_tool_names(
                service,
                workbench,
                _edit_mode_from_runtime_state(runtime_state),
            )
        ),
    }


def _surface_authorization_tuple(
    workbench: str | None,
    engine: str,
    surface_id: str,
) -> tuple[str, str, str]:
    """Normalize presentation-equivalent ribbons to one authorization key."""

    clean_workbench = str(workbench or "")
    return (
        "ModelAssemblyAuthoring"
        if is_model_assembly_workbench(clean_workbench)
        else clean_workbench,
        str(engine or ""),
        str(surface_id or ""),
    )


def _scripted_engines_in_tool_names(names: list[str]) -> list[str]:
    return [
        engine
        for engine in SCRIPTED_ENGINE_PROVIDER_TOOLS
        if any(name.startswith(f"{engine}.") for name in names)
    ]


def _turn_start_tool_surface(
    workbench: str | None,
    schemas: list[dict[str, Any]],
    *,
    resolution: ModelingSurface | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    """Validate and freeze the complete provider surface for one turn.

    ChatGPT dynamic tool declarations cannot change after the app-server thread
    starts. Every attempted call is reauthorized against the live engine and
    workbench tuple by the session tool runner.
    """
    try:
        schema_json_bytes = len(
            json.dumps(
                schemas,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"The turn-start provider schemas are not JSON serializable: {exc}"
        ) from exc
    if schema_json_bytes > MAX_PROVIDER_TOOL_SCHEMAS_JSON_BYTES:
        raise ValueError(
            "The exact turn-start provider schemas exceed the deterministic "
            f"{MAX_PROVIDER_TOOL_SCHEMAS_JSON_BYTES}-byte wire limit "
            f"({schema_json_bytes} bytes)."
        )
    if not schemas:
        reason = resolution.unavailable_reason if resolution is not None else ""
        raise ValueError(reason or "The turn-start provider surface has no tools.")
    if any(not isinstance(schema, dict) for schema in schemas):
        raise ValueError("Every turn-start provider tool schema must be an object.")
    names = [str(schema.get("name") or "").strip() for schema in schemas]
    if any(not name for name in names):
        raise ValueError("Every turn-start provider tool schema must have a name.")
    if len(names) != len(set(names)):
        raise ValueError("The turn-start provider surface contains duplicate tools.")
    resolved_engine = str(engine or "").strip().lower()
    if resolution is not None:
        if resolved_engine and resolved_engine != resolution.engine:
            raise ValueError(
                "The requested engine does not match the resolved surface."
            )
        resolved_engine = resolution.engine
    if not resolved_engine:
        resolved_engine = infer_engine_from_names(names)
    if (
        resolved_engine == "vibescript"
        and schema_json_bytes > MAX_VIBESCRIPT_TOOL_SCHEMAS_JSON_BYTES
    ):
        raise ValueError(
            "The exact VibeScript provider schemas exceed the tactical "
            f"{MAX_VIBESCRIPT_TOOL_SCHEMAS_JSON_BYTES}-byte wire limit "
            f"({schema_json_bytes} bytes)."
        )
    if resolution is None:
        from VibeCADModelingSurface import resolve_modeling_surface

        resolution = resolve_modeling_surface(workbench, resolved_engine)
    validate_surface_names(
        workbench=workbench,
        engine=resolved_engine,
        names=names,
        allowed_names=resolution.tool_names,
    )
    return {
        "kind": "turn_start_snapshot",
        "frozen": True,
        "workbench": str(workbench or ""),
        "engine": resolved_engine,
        "domain": resolution.domain,
        "surface_id": resolution.surface_id,
        "available": resolution.available,
        "unavailable_reason": resolution.unavailable_reason,
        "tool_names": names,
        "schema_count": len(schemas),
        "schema_sha256": provider_tool_schema_digest(schemas),
    }


def _provider_schema_copy(schema: dict[str, Any]) -> dict[str, Any]:
    """Return only the callable contract that a provider model needs."""

    def compact(value: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [compact(item, path + ("[]",)) for item in value]
        if not isinstance(value, dict):
            return value
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "default":
                continue
            if key == "description":
                if len(path) == 2 and path[0] == "properties":
                    result[key] = item
                continue
            result[key] = compact(item, path + (str(key),))
        return result

    parameters = schema.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError(f"Provider tool {schema.get('name')!r} has no parameters.")
    return {
        "name": str(schema.get("name") or ""),
        "description": str(schema.get("description") or ""),
        "parameters": compact(parameters),
    }


def _minimal_runtime_state(service: VibeCADService) -> dict[str, Any]:
    """Read edit ownership only; never recompute or summarize geometry."""

    getter = getattr(service, "provider_edit_object_summary", None)
    edit_object = getter() if callable(getter) else None
    if not isinstance(edit_object, dict):
        return {"edit_mode": False, "active_sketch": None}
    is_sketch = str(edit_object.get("type") or "") == "Sketcher::SketchObject"
    return {
        "edit_mode": True,
        "edit_object": edit_object,
        "active_sketch": (
            {"name": str(edit_object.get("name") or "")} if is_sketch else None
        ),
    }


_COMBINED_SOURCE_INDEX_MARKER = (
    "_vibecad_deferred_combined_vibescript_program_index"
)


def _capture_editable_sources_for_workbench(
    service: VibeCADService,
    workbench: str | None,
) -> dict[str, Any]:
    """Capture active or unified Model/Assembly source identities."""

    active_pack = vibescript_domains.get_vibescript_pack(workbench)
    if active_pack is None:
        raise RuntimeError("The active workbench has no editable VibeScript domain.")
    if not is_model_assembly_workbench(workbench):
        return vibescript_domains.capture_editable_sources_snapshot(
            service,
            active_pack.domain,
        )
    snapshots = []
    for domain in MODEL_ASSEMBLY_DOMAINS:
        pack = vibescript_domains.get_vibescript_pack_for_domain(domain)
        if pack is None:
            continue
        snapshots.append(
            vibescript_domains.capture_editable_sources_snapshot(
                _DomainBoundService(service, pack),
                domain,
            )
        )
    return {
        _COMBINED_SOURCE_INDEX_MARKER: True,
        "active_domain": active_pack.domain,
        "active_workbench": active_pack.workbench,
        "snapshots": snapshots,
    }


def _complete_editable_sources_for_workbench(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Complete one source index while preserving its v1 active-domain view."""

    if snapshot.get(_COMBINED_SOURCE_INDEX_MARKER) is not True:
        return vibescript_domains.complete_editable_sources_snapshot(snapshot)
    completed = [
        vibescript_domains.complete_editable_sources_snapshot(candidate)
        for candidate in list(snapshot.get("snapshots") or [])
        if isinstance(candidate, Mapping)
    ]
    active_domain = str(snapshot.get("active_domain") or "")
    active = next(
        (
            candidate
            for candidate in completed
            if str(candidate.get("domain") or "") == active_domain
        ),
        None,
    )
    if active is None:
        raise RuntimeError(
            f"The unified source index has no active {active_domain!r} domain."
        )
    result = dict(active)
    all_sources = sorted(
        (
            dict(source)
            for candidate in completed
            for source in list(candidate.get("sources") or [])
            if isinstance(source, Mapping)
        ),
        key=lambda item: (
            str(item.get("domain") or ""),
            str(item.get("source_id") or ""),
        ),
    )
    result.update(
        {
            "authoring_domains": [
                str(candidate.get("domain") or "") for candidate in completed
            ],
            "all_source_count": len(all_sources),
            "all_sources": all_sources,
            "domain_source_counts": {
                str(candidate.get("domain") or ""): int(
                    candidate.get("source_count") or 0
                )
                for candidate in completed
            },
        }
    )
    tools = dict(result.get("tools") or {})
    tools["create_program_arguments"] = {
        "domain": "partdesign or assembly",
    }
    tools["read_api_arguments"] = {
        "domain": "partdesign or assembly",
        "names": ["exact_callable_name"],
    }
    result["tools"] = tools
    return result


def _rebase_unified_source_index(
    source_index: Mapping[str, Any],
    workbench: str | None,
) -> dict[str, Any]:
    """Keep the v1 source slice aligned with a frozen ribbon default."""

    result = dict(source_index)
    if not is_model_assembly_workbench(workbench) or "all_sources" not in result:
        return result
    pack = vibescript_domains.get_vibescript_pack(workbench)
    if pack is None:
        return result
    sources = [
        dict(candidate)
        for candidate in list(result.get("all_sources") or [])
        if isinstance(candidate, Mapping)
        and str(candidate.get("domain") or "") == pack.domain
    ]
    result.update(
        {
            "domain": pack.domain,
            "workbench": pack.workbench,
            "source_count": len(sources),
            "sources": sources,
        }
    )
    return result


def _capture_context_for_provider(
    service: VibeCADService,
    session_trigger: dict[str, Any] | None = None,
    prepared_component_catalog: Mapping[str, Any] | None = None,
    prepared_native_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw_context = service.provider_context_summary()
    allowed_turn_facts = (
        "document",
        "selection",
        "view_screenshot",
        "reference_images",
        "aero",
    )
    context = {
        key: raw_context[key] for key in allowed_turn_facts if key in raw_context
    }
    workbench = service.active_workbench_name()
    native_provider_surface = None
    captured_native_state = None
    native_engine = provider_engine_from_service(service) == "native"
    if native_engine:
        from VibeCADNativeProviderContext import (
            native_active_state,
            provider_authorized_native_surface,
            provider_visible_native_state,
            resolve_production_native_surface,
        )

        native_registry, native_provider_surface = resolve_production_native_surface()
        resolution = modeling_surface_from_native_provider(
            workbench,
            native_provider_surface,
        )
        if resolution.available:
            captured_native_state = (
                dict(prepared_native_state)
                if prepared_native_state is not None
                else native_active_state(service)
            )
            native_provider_surface = provider_authorized_native_surface(
                native_provider_surface,
                captured_native_state,
                registry=native_registry,
            )
            resolution = modeling_surface_from_native_provider(
                workbench,
                native_provider_surface,
            )
    else:
        resolution = resolve_service_surface(service, workbench)
    context["workbench"] = workbench
    context["modeling_surface"] = {
        "workbench": str(resolution.workbench or ""),
        "engine": resolution.engine,
        "domain": resolution.domain,
        "surface_id": resolution.surface_id,
        "available": resolution.available,
        **(
            {
                "authoring_domains": list(MODEL_ASSEMBLY_DOMAINS),
                "default_domain": resolution.domain,
                "workbench_presentation_only": True,
            }
            if is_model_assembly_workbench(workbench)
            else {}
        ),
        **(
            {"unavailable_reason": resolution.unavailable_reason}
            if not resolution.available
            else {}
        ),
    }
    if resolution.engine == "native":
        context.pop("document", None)
        context.pop("selection", None)
        if resolution.available:
            context["native_state"] = provider_visible_native_state(
                captured_native_state
            )
    if resolution.engine == "vibescript" and resolution.available and resolution.domain:
        context["editable_sources"] = _capture_editable_sources_for_workbench(
            service,
            workbench,
        )
        if resolution.domain in {"partdesign", "assembly", "robot"}:
            if prepared_component_catalog is not None:
                context["_vibecad_component_catalog"] = dict(prepared_component_catalog)
            else:
                from VibeCADComponentCatalog import (
                    ComponentCatalogError,
                    capture_component_catalog,
                )

                try:
                    context["_vibecad_component_catalog"] = (
                        capture_component_catalog(service)
                    )
                except ComponentCatalogError as exc:
                    if str(exc) != "Component search requires an active document.":
                        raise
    context["_vibecad_debug"] = service.provider_debug_config()
    runtime_state = _minimal_runtime_state(service)
    if native_provider_surface is not None:
        from VibeCADNativeProviderContext import (
            schemas_for_native_provider_surface,
        )
        from VibeCADNativeCapabilityRegistry import _provider_schema_operations
        from VibeCADNativeTurn import native_operation_scope_digest

        exact_native_schemas = [
            dict(schema) for schema in native_provider_surface.schemas
        ]
        schemas = schemas_for_native_provider_surface(native_provider_surface)
        operations_by_tool = {
            str(schema.get("name") or ""): list(operations)
            for schema in exact_native_schemas
            if (operations := _provider_schema_operations(schema))
        }
        provider_schema_digest = provider_tool_schema_digest(schemas)
        native_turn_authorization = {
            # Keep schema_sha256 as a compatibility alias. Both names now
            # unambiguously refer to the schemas actually sent to the provider.
            "schema_sha256": provider_schema_digest,
            "provider_schema_sha256": provider_schema_digest,
            "operation_scope_sha256": native_operation_scope_digest(
                operations_by_tool
            ),
            "operations_by_tool": operations_by_tool,
        }
    elif not native_engine:
        schemas = provider_tool_schemas(
            service,
            workbench,
            runtime_state=runtime_state,
        )
    else:
        schemas = []
    context["provider_tool_schemas"] = schemas
    context["provider_tool_surface"] = _turn_start_tool_surface(
        workbench,
        schemas,
        resolution=resolution,
    )
    if native_provider_surface is not None:
        context["_native_turn_authorization"] = native_turn_authorization
    if session_trigger:
        context["session_trigger"] = dict(session_trigger)
    return context


def _complete_context_for_provider(context: Mapping[str, Any]) -> dict[str, Any]:
    """Complete artifact-backed context after leaving the document thread."""

    completed = dict(context)
    editable_sources = completed.get("editable_sources")
    if (
        isinstance(editable_sources, Mapping)
        and (
            editable_sources.get("_vibecad_deferred_vibescript_program_index")
            is True
            or editable_sources.get(_COMBINED_SOURCE_INDEX_MARKER) is True
        )
    ):
        completed["editable_sources"] = _complete_editable_sources_for_workbench(
            editable_sources
        )
    component_catalog = completed.get("_vibecad_component_catalog")
    if isinstance(component_catalog, Mapping):
        from VibeCADComponentCatalog import (
            component_inventory,
            prepare_captured_component_catalog,
        )

        prepared = (
            dict(component_catalog)
            if component_catalog.get("schema")
            == "vibecad-component-catalog-snapshot-v1"
            else prepare_captured_component_catalog(component_catalog)
        )
        completed["_vibecad_component_catalog"] = prepared
        inventory = component_inventory(prepared)
        completed["available_components"] = inventory
        editable = completed.get("editable_sources")
        if isinstance(editable, Mapping):
            component_sources: dict[tuple[str, str, str, str], dict[str, Any]] = {}
            for component in list(inventory.get("components") or []):
                if not isinstance(component, Mapping):
                    continue
                authoring = component.get("authoring_source")
                if not isinstance(authoring, Mapping):
                    continue
                source_id = str(authoring.get("source_id") or "")
                output_name = str(authoring.get("output_name") or "")
                program = authoring.get("program")
                if not source_id or not str(program or ""):
                    continue
                item = {
                    key: value
                    for key, value in dict(authoring).items()
                    if key not in {"source_id", "document_uid", "current_revision"}
                }
                item["read_source"] = {
                    "tool": "vibescript.read_source",
                    "arguments": {"program": str(program), "include_logs": False},
                }
                item["read_api"] = {
                    "tool": "vibescript.read_api",
                    "arguments": {"program": str(program)},
                }
                item["edit_source"] = {
                    "tool": "vibescript.edit_source",
                    "target_arguments": {
                        "program": str(program),
                        **(
                            {"expected_revision": str(authoring["current_revision"])}
                            if authoring.get("current_revision")
                            else {}
                        ),
                    },
                }
                document, domain, name = _program_reference_key(program)
                component_sources[(document, domain, name, output_name)] = item
            updated_editable = dict(editable)
            updated_editable["component_sources"] = list(component_sources.values())
            updated_editable["component_source_count"] = len(component_sources)
            updated_editable["component_source_rule"] = (
                "Read and edit a component through its exact program reference. The source's "
                "owning workbench and open document are selected automatically; the "
                "visible Assembly workbench does not change."
            )
            completed["editable_sources"] = updated_editable
    return completed


def _context_for_provider(
    service: VibeCADService,
    session_trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return completed provider context for synchronous compatibility callers."""

    return _complete_context_for_provider(
        _capture_context_for_provider(
            service,
            session_trigger,
        )
    )


def _build_context_for_provider(
    service: VibeCADService,
    session_trigger: dict[str, Any] | None,
    document_thread_dispatch: DocumentThreadDispatch | None,
    prepared_component_catalog: Mapping[str, Any] | None = None,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    prepared_native_state = _build_responsive_analyze_native_state(
        service,
        document_thread_dispatch,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )
    if prepared_native_state is None:
        prepared_native_state = _build_responsive_drawing_native_state(
            service,
            document_thread_dispatch,
            cancellation_check=cancellation_check,
            progress_callback=progress_callback,
        )
    captured = _on_document_thread(
        document_thread_dispatch,
        lambda: _capture_context_for_provider(
            service,
            session_trigger,
            prepared_component_catalog,
            prepared_native_state,
        ),
    )
    return _complete_context_for_provider(captured)


def _build_responsive_analyze_native_state(
    service: VibeCADService,
    document_thread_dispatch: DocumentThreadDispatch | None,
    *,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any] | None:
    """Build Analyze state in bounded UI dispatches from a provider worker."""

    from VibeCADNativeAnalyzeContext import (
        AnalyzeContextStale,
        capture_responsive_analyze_snapshot,
    )
    from VibeCADNativeAnalyzeSnapshot import (
        discard_analyze_snapshot_capture,
        finish_analyze_snapshot_capture,
        validate_analyze_snapshot_geometry,
    )
    from VibeCADNativeSnapshot import complete_active_snapshot

    begin = getattr(service, "begin_native_analyze_context_request", None)
    coordinator_reader = getattr(service, "native_analyze_context_coordinator", None)
    capture_batch = getattr(service, "capture_native_analyze_context_batch", None)
    capture_clipping = getattr(
        service,
        "capture_native_analyze_context_clipping",
        None,
    )
    if not all(
        callable(callback)
        for callback in (begin, coordinator_reader, capture_batch, capture_clipping)
    ):
        return None

    def emit_progress(percent: int, message: str) -> None:
        _emit(
            progress_callback,
            {
                "event": "analyze_context_progress",
                "percent": int(percent),
                "message": str(message or ""),
            },
        )

    for attempt in range(2):
        request = _on_document_thread(document_thread_dispatch, begin)
        if request is None:
            return None
        if not isinstance(request, Mapping):
            raise RuntimeError("Analyze context request returned no object.")
        uid = str(request.get("document_uid") or "")
        revision = int(request.get("structural_revision", 0) or 0)
        cache_variant = str(request.get("active_analysis_name") or "")
        coordinator = coordinator_reader()

        def build(cancelled, report):
            try:
                return capture_responsive_analyze_snapshot(
                    request,
                    dispatch_to_document_thread=lambda operation: _on_document_thread(
                        document_thread_dispatch,
                        operation,
                    ),
                    capture_batch=capture_batch,
                    capture_clipping=capture_clipping,
                    finalize=finish_analyze_snapshot_capture,
                    cancellation_check=cancelled,
                    progress_callback=report,
                    postprocess_parts=validate_analyze_snapshot_geometry,
                )
            finally:
                discard_analyze_snapshot_capture(request)

        try:
            if request.get("cacheable") is False:
                domain = build(
                    lambda: bool(
                        cancellation_check is not None and cancellation_check()
                    ),
                    emit_progress,
                )
                _emit(
                    progress_callback,
                    {
                        "event": "analyze_context_ready",
                        "structural_revision": revision,
                    },
                )
                return complete_active_snapshot(request["base_snapshot"], domain)
            cache_hit = coordinator.has_cached(
                uid,
                revision,
                variant=cache_variant,
            )
            domain = coordinator.get_or_build(
                uid,
                revision,
                build,
                variant=cache_variant,
                cancellation_check=cancellation_check,
                progress_callback=emit_progress,
            )
            if cache_hit:
                detached_clipping = request.get("detached_clipping")
                current_clipping = (
                    deepcopy(dict(detached_clipping))
                    if isinstance(detached_clipping, Mapping)
                    else _on_document_thread(
                        document_thread_dispatch,
                        lambda: capture_clipping(request),
                    )
                )
                domain = dict(domain)
                domain["clipping"] = dict(current_clipping)
                _emit(
                    progress_callback,
                    {
                        "event": "analyze_context_cache_hit",
                        "structural_revision": revision,
                    },
                )
            else:
                _emit(
                    progress_callback,
                    {
                        "event": "analyze_context_ready",
                        "structural_revision": revision,
                    },
                )
            return complete_active_snapshot(request["base_snapshot"], domain)
        except AnalyzeContextStale:
            if attempt == 0 and not (
                cancellation_check is not None and cancellation_check()
            ):
                continue
            raise
    raise RuntimeError("Analyze context changed repeatedly during capture.")


def prewarm_analyze_context(
    service: VibeCADService,
    document_thread_dispatch: DocumentThreadDispatch | None,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any] | None:
    """Populate the read-only Analyze cache before the human presses Send."""

    return _build_responsive_analyze_native_state(
        service,
        document_thread_dispatch,
        progress_callback=progress_callback,
    )


def _prepare_responsive_drawing_source_catalog(
    service: VibeCADService,
    document_thread_dispatch: DocumentThreadDispatch | None,
    *,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any] | None:
    """Prepare one complete Drawing source catalog in bounded Qt batches."""

    from VibeCADNativeDrawingContext import (
        DrawingContextStale,
        capture_responsive_drawing_source_catalog,
    )
    from VibeCADNativeDrawingSourceCatalog import (
        cache_drawing_source_catalog_state,
        drawing_source_catalog_is_cached,
    )

    begin = getattr(service, "begin_native_drawing_context_request", None)
    coordinator_reader = getattr(service, "native_drawing_context_coordinator", None)
    capture_batch = getattr(service, "capture_native_drawing_context_batch", None)
    if not all(
        callable(callback)
        for callback in (begin, coordinator_reader, capture_batch)
    ):
        return None

    def emit_progress(percent: int, message: str) -> None:
        _emit(
            progress_callback,
            {
                "event": "drawing_context_progress",
                "percent": int(percent),
                "message": str(message or ""),
            },
        )

    for attempt in range(2):
        request = _on_document_thread(document_thread_dispatch, begin)
        if request is None:
            return None
        if not isinstance(request, Mapping):
            raise RuntimeError("Drawing context request returned no object.")
        uid = str(request.get("document_uid") or "")
        revision = int(request.get("structural_revision", 0) or 0)
        if drawing_source_catalog_is_cached(uid, revision):
            _emit(
                progress_callback,
                {
                    "event": "drawing_context_cache_hit",
                    "structural_revision": revision,
                },
            )
            return dict(request)
        coordinator = coordinator_reader()

        def build(cancelled, report):
            if drawing_source_catalog_is_cached(uid, revision):
                return {"structural_revision": revision, "cache_hit": True}
            return capture_responsive_drawing_source_catalog(
                request,
                dispatch_to_document_thread=lambda operation: _on_document_thread(
                    document_thread_dispatch,
                    operation,
                ),
                capture_batch=capture_batch,
                finalize=lambda current, sources: cache_drawing_source_catalog_state(
                    str(current["document_uid"]),
                    int(current["structural_revision"]),
                    list(sources),
                ),
                cancellation_check=cancelled,
                progress_callback=report,
            )

        try:
            coordinator.get_or_build(
                uid,
                revision,
                build,
                cancellation_check=cancellation_check,
                progress_callback=emit_progress,
            )
            if not drawing_source_catalog_is_cached(uid, revision):
                raise RuntimeError(
                    "Drawing source preparation completed without a cached catalog."
                )
            _emit(
                progress_callback,
                {
                    "event": "drawing_context_ready",
                    "structural_revision": revision,
                },
            )
            return dict(request)
        except DrawingContextStale:
            if attempt == 0 and not (
                cancellation_check is not None and cancellation_check()
            ):
                continue
            raise
    raise RuntimeError("Drawing context changed repeatedly during source capture.")


def _build_responsive_drawing_native_state(
    service: VibeCADService,
    document_thread_dispatch: DocumentThreadDispatch | None,
    *,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any] | None:
    """Build Drawing state after responsive source preparation."""

    from VibeCADNativeDrawingContext import DrawingContextStale

    finish = getattr(service, "finish_native_drawing_context_request", None)
    if not callable(finish):
        return None
    for attempt in range(2):
        request = _prepare_responsive_drawing_source_catalog(
            service,
            document_thread_dispatch,
            cancellation_check=cancellation_check,
            progress_callback=progress_callback,
        )
        if request is None:
            return None
        completed = request.get("completed_snapshot")
        if isinstance(completed, Mapping):
            return deepcopy(dict(completed))
        try:
            return _on_document_thread(
                document_thread_dispatch,
                lambda: finish(request),
            )
        except DrawingContextStale:
            if attempt == 0 and not (
                cancellation_check is not None and cancellation_check()
            ):
                continue
            raise
    raise RuntimeError("Drawing context changed repeatedly during finalization.")


def prewarm_drawing_context(
    service: VibeCADService,
    document_thread_dispatch: DocumentThreadDispatch | None,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any] | None:
    """Populate the Drawing source cache before the human presses Send."""

    return _build_responsive_drawing_native_state(
        service,
        document_thread_dispatch,
        progress_callback=progress_callback,
    )


def _consume_context_view_attachment(
    service: VibeCADService,
    context: Mapping[str, Any],
    dispatch: DocumentThreadDispatch | None,
) -> None:
    """Consume the exact one-shot viewport already copied into provider context.

    Human reference images are durable context and deliberately remain
    attached across turns until the human removes them.
    """

    screenshot = context.get("view_screenshot")
    consume = getattr(service, "consume_view_screenshot_attachment", None)
    if (
        isinstance(screenshot, dict)
        and screenshot.get("captured") is True
        and screenshot.get("pending_attachment") is True
        and callable(consume)
    ):
        frozen = dict(screenshot)
        _on_document_thread(dispatch, lambda: consume(frozen))


def _persist_session_conversation_turn(
    service: VibeCADService,
    role: str,
    content: str,
    *,
    provider: str | None = None,
    metadata: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    dispatch: DocumentThreadDispatch | None = None,
) -> dict[str, Any]:
    """Persist text off-thread after a document-thread identity capture."""

    prepare = getattr(service, "prepare_conversation_turn", None)
    persist = getattr(service, "persist_prepared_conversation_turn", None)
    accept = getattr(service, "accept_persisted_conversation_turn", None)
    if not all(callable(item) for item in (prepare, persist, accept)):
        raise RuntimeError(
            "The VibeCAD service does not implement the asynchronous "
            "conversation persistence contract."
        )
    prepared = _on_document_thread(
        dispatch,
        lambda: prepare(
            role,
            content,
            provider=provider,
            metadata=metadata,
            conversation_id=conversation_id,
        ),
    )
    history = persist(prepared)
    _on_document_thread(dispatch, lambda: accept(history, prepared))
    return history


def _load_conversation_for_session(
    service: VibeCADService,
    dispatch: DocumentThreadDispatch | None,
) -> dict[str, Any]:
    """Read the selected conversation without doing artifact I/O on Qt's thread."""

    prepare = getattr(service, "prepare_conversation_history_read", None)
    complete = getattr(service, "complete_conversation_history_read", None)
    accept = getattr(service, "accept_conversation_history_read", None)
    if not all(callable(item) for item in (prepare, complete, accept)):
        history = _on_document_thread(dispatch, service.conversation_history)
        return dict(history) if isinstance(history, dict) else {"conversation": []}

    prepared = _on_document_thread(dispatch, prepare)
    history = complete(prepared)
    accepted = _on_document_thread(dispatch, lambda: accept(prepared, history))
    if isinstance(accepted, dict) and accepted.get("accepted") is False:
        raise RuntimeError(
            "The active conversation changed while VibeCAD loaded its history. "
            "Start the request again in the selected conversation."
        )
    return dict(history) if isinstance(history, dict) else {"conversation": []}


_PROVIDER_REDUNDANT_SOURCE_FIELDS = frozenset(
    {
        "read_tool",
        "read_arguments",
        "build_tool",
        "build_arguments",
        "edit_tool",
        "edit_target_arguments",
        "delete_output_tool",
        "delete_program_tool",
    }
)


def _provider_editable_sources_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove tool-schema duplication from the model-visible source index."""

    if int(value.get("source_count") or 0) == 0:
        return {
            key: value[key]
            for key in ("schema", "domain", "source_count")
            if key in value
        }
    result = {
        key: item
        for key, item in value.items()
        if key != "tools"
        and not (
            key in {"sources_truncated", "sources_omitted"}
            and item in (False, 0)
        )
    }
    for collection_name in ("sources", "all_sources", "component_sources"):
        collection = result.get(collection_name)
        if not isinstance(collection, list):
            continue
        result[collection_name] = [
            {
                key: item
                for key, item in source.items()
                if key not in _PROVIDER_REDUNDANT_SOURCE_FIELDS
            }
            for source in collection
            if isinstance(source, Mapping)
        ]
    return result


def _provider_component_inventory_payload(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep empty component discovery truthful without its unused instructions."""

    if int(value.get("component_count") or 0) != 0:
        result = dict(value)
        result.pop("usage", None)
        components = []
        for raw_component in list(value.get("components") or []):
            if not isinstance(raw_component, Mapping):
                continue
            component = {
                key: raw_component[key]
                for key in ("catalog_key", "label")
                if key in raw_component
            }
            interfaces = []
            for raw_interface in list(raw_component.get("interfaces") or []):
                if not isinstance(raw_interface, Mapping):
                    continue
                interface = {
                    key: raw_interface[key]
                    for key in ("name", "description")
                    if key in raw_interface and raw_interface[key] not in (None, "")
                }
                raw_connector = raw_interface.get("connector")
                if isinstance(raw_connector, Mapping):
                    connector = {
                        key: raw_connector[key]
                        for key in (
                            "kind",
                            "allowed_joints",
                            "compatibility",
                            "pitch_radius_mm",
                            "thread_pitch_mm",
                        )
                        if key in raw_connector
                        and raw_connector[key] not in (None, "", [], {})
                    }
                    allowed = connector.get("allowed_joints")
                    if isinstance(allowed, list):
                        connector["allowed_joints"] = [
                            "gear" if kind == "gears" else kind
                            for kind in allowed
                        ]
                    compatibility = connector.get("compatibility")
                    if isinstance(compatibility, Mapping):
                        connector["compatibility"] = {
                            "gear" if kind == "gears" else str(kind): token
                            for kind, token in compatibility.items()
                        }
                    interface["connector"] = connector
                interfaces.append(interface)
            if interfaces:
                component["interfaces"] = interfaces
            if raw_component.get("interfaces_truncated") is True:
                component["interfaces_truncated"] = True
            components.append(component)
        result["components"] = components
        return result
    return {
        key: value[key]
        for key in (
            "schema",
            "component_count",
            "project_file_search_available",
            "catalog_health",
        )
        if key in value and value[key] not in (None, "", [], {})
    }


class _ComponentCatalogInputError(ValueError):
    def __init__(self, catalog_key: str, available_keys: list[str]) -> None:
        self.catalog_key = catalog_key
        self.available_keys = available_keys
        super().__init__(f"Unknown component catalog key: {catalog_key}")


def _resolve_component_catalog_inputs(
    arguments: Mapping[str, Any],
    component_catalog: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve turn-local component keys to frozen durable references."""

    resolved = dict(arguments)
    inputs = arguments.get("inputs")
    if not isinstance(inputs, Mapping):
        return resolved
    if not any(
        isinstance(value, Mapping) and set(value) == {"catalog_key"}
        for value in inputs.values()
    ):
        return resolved

    catalog = dict(component_catalog or {})
    if catalog.get("schema") == "vibecad-component-catalog-snapshot-v1":
        from VibeCADComponentCatalog import component_catalog_reference_map

        references = component_catalog_reference_map(catalog)
    else:
        components = [
            dict(value)
            for value in list(catalog.get("components") or [])
            if isinstance(value, Mapping)
        ]
        references = {
            str(component.get("catalog_key") or ""): dict(component["reference"])
            for component in components
            if str(component.get("catalog_key") or "")
            and isinstance(component.get("reference"), Mapping)
        }
    available_keys = sorted(references)
    resolved_inputs = dict(inputs)
    for name, value in inputs.items():
        if not isinstance(value, Mapping) or set(value) != {"catalog_key"}:
            continue
        catalog_key = str(value.get("catalog_key") or "").strip()
        if catalog_key not in references:
            raise _ComponentCatalogInputError(catalog_key, available_keys)
        resolved_inputs[name] = dict(references[catalog_key])
    resolved["inputs"] = resolved_inputs
    return resolved


def _provider_state_payload(context: dict[str, Any]) -> dict[str, Any]:
    aero = context.get("aero")
    include_aero = aero not in (None, "", [], {}) and not (
        isinstance(aero, Mapping) and aero.get("available") is False
    )
    modeling_surface = context.get("modeling_surface")
    if (
        isinstance(modeling_surface, Mapping)
        and str(modeling_surface.get("engine") or "").strip().lower() == "native"
    ):
        result = _model_visible_context(context)
        result.pop("view_screenshot", None)
        result.pop("reference_images", None)
        if include_aero:
            result["aero"] = aero
        return result
    # Final first-prompt allowlist. This dict is serialized as
    # VIBECAD_CONTEXT_JSON. Aero is a sibling of document/selection, not a
    # field on provider_turn_document_summary, and it is not delivered by
    # steering.
    keys = (
        "workbench",
        "modeling_surface",
        "native_state",
        "document",
        "selection",
        "editable_sources",
        "available_components",
        "aero",
    )
    result = {
        key: context[key]
        for key in keys
        if key in context and context[key] not in (None, "", [], {})
    }
    if not include_aero:
        result.pop("aero", None)
    editable_sources = result.get("editable_sources")
    if isinstance(editable_sources, Mapping):
        result["editable_sources"] = _provider_editable_sources_payload(
            editable_sources
        )
    components = result.get("available_components")
    if isinstance(components, Mapping):
        result["available_components"] = _provider_component_inventory_payload(
            components
        )
    return result


def _bounded_conversation_content(content: str) -> tuple[str, bool]:
    clean = str(content or "").strip()
    if len(clean) <= MAX_RECENT_CONVERSATION_TURN_CHARACTERS:
        return clean, False

    marker = "\n...[middle of this earlier message omitted]...\n"
    remaining = MAX_RECENT_CONVERSATION_TURN_CHARACTERS - len(marker)
    head = remaining // 2
    tail = remaining - head
    return clean[:head] + marker + clean[-tail:], True


def _recent_conversation_payload(
    conversation: list[dict[str, Any]] | None,
    *,
    current_user_message: str | None = None,
) -> dict[str, Any]:
    """Return a chronological, bounded window from the selected conversation."""

    cleaned: list[dict[str, str]] = []
    for item in conversation or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        metadata = item.get("metadata")
        if (
            role == "user"
            and isinstance(metadata, dict)
            and str(metadata.get("source") or "").strip().lower() == "stop"
        ):
            # The Stop button is a transport control for the interrupted run,
            # not a durable design instruction for a later run.
            continue
        cleaned.append({"role": role, "content": content})

    current = str(current_user_message or "").strip()
    if (
        current
        and cleaned
        and cleaned[-1]["role"] == "user"
        and cleaned[-1]["content"] == current
    ):
        # The normal prompt path persists the user turn before starting the
        # provider. Keep it in durable history, but do not send it twice.
        cleaned.pop()

    candidates = cleaned[-MAX_RECENT_CONVERSATION_TURNS:]
    selected: list[dict[str, str]] = []
    truncated_turn_count = 0
    for item in reversed(candidates):
        content, truncated = _bounded_conversation_content(item["content"])
        candidate = {"role": item["role"], "content": content}
        trial = [candidate, *selected]
        trial_payload = {
            "turns": trial,
            "omitted_turn_count": len(cleaned) - len(trial),
            "truncated_turn_count": truncated_turn_count + int(truncated),
        }
        encoded = json.dumps(
            trial_payload,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > MAX_RECENT_CONVERSATION_JSON_BYTES:
            break
        selected = trial
        truncated_turn_count += int(truncated)

    return {
        "turns": selected,
        "omitted_turn_count": len(cleaned) - len(selected),
        "truncated_turn_count": truncated_turn_count,
    }


def _provider_prompt(
    prompt: str,
    context: dict[str, Any],
    *,
    prompt_section: str = "CURRENT_USER_MESSAGE",
    recent_conversation: list[dict[str, Any]] | None = None,
    current_user_message: str | None = None,
) -> str:
    active_state = _provider_state_payload(context)
    authoring_contract: dict[str, Any] | None = None
    editable_sources = active_state.get("editable_sources")
    if isinstance(editable_sources, Mapping):
        core_api = editable_sources.get("core_api")
        if isinstance(core_api, Mapping) and core_api:
            authoring_contract = dict(core_api)
            compact_sources = dict(editable_sources)
            compact_sources.pop("core_api", None)
            active_state["editable_sources"] = compact_sources
    payload = {"active_state": active_state}
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str)
    encoded_bytes = len(encoded.encode("utf-8"))
    if encoded_bytes > MAX_TURN_CONTEXT_JSON_BYTES:
        raise RuntimeError(
            "Deterministic VibeCAD turn-start context exceeded "
            f"{MAX_TURN_CONTEXT_JSON_BYTES} bytes ({encoded_bytes} bytes)."
        )
    conversation_payload = _recent_conversation_payload(
        recent_conversation,
        current_user_message=current_user_message,
    )
    encoded_conversation = json.dumps(
        conversation_payload,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    conversation_bytes = len(encoded_conversation.encode("utf-8"))
    if conversation_bytes > MAX_RECENT_CONVERSATION_JSON_BYTES:
        raise RuntimeError(
            "VibeCAD recent conversation window exceeded "
            f"{MAX_RECENT_CONVERSATION_JSON_BYTES} bytes "
            f"({conversation_bytes} bytes)."
        )
    authoring_section = (
        "VIBESCRIPT_AUTHORING_CONTRACT_JSON\n"
        + json.dumps(
            authoring_contract,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\nEND_VIBESCRIPT_AUTHORING_CONTRACT_JSON\n\n"
        if authoring_contract is not None
        else ""
    )
    return (
        "VIBECAD_CONTEXT_JSON\n"
        + encoded
        + "\nEND_VIBECAD_CONTEXT_JSON\n\n"
        + "RECENT_CONVERSATION_JSON\n"
        + encoded_conversation
        + "\nEND_RECENT_CONVERSATION_JSON\n\n"
        + authoring_section
        + f"{prompt_section}\n"
        + prompt
    )


def _run_provider(
    provider: BaseProvider,
    prompt: str,
    context: dict[str, Any],
    tool_runner: Callable[[str, str, str], dict[str, Any]],
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
):
    return provider.run(
        prompt,
        context,
        tool_runner=tool_runner,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )


def _parse_arguments(arguments_json: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(arguments_json or "{}")
    except (TypeError, ValueError) as exc:
        return None, f"Tool arguments are not valid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "Tool arguments must be a JSON object."
    return value, None


def _active_sketch_name(state: dict[str, Any]) -> str:
    sketch = state.get("active_sketch")
    if not isinstance(sketch, dict):
        return ""
    return str(sketch.get("name") or "").strip()


def _edit_mode_block(
    tool: Any,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    edit_mode = (
        "sketch" if state.get("edit_mode") and _active_sketch_name(state) else "none"
    )
    if tool.spec.supports_edit_mode(edit_mode):
        return None
    if edit_mode == "sketch":
        explanation = (
            f"Sketch {_active_sketch_name(state)} is open for editing. Ask the "
            f"user to finish or close that Sketch in the UI, then continue in a "
            f"new turn before running {tool.name}."
        )
    else:
        explanation = (
            f"{tool.name} requires an open Sketcher edit session. Open the exact "
            "target sketch first."
        )
    return tool_failure(
        tool.name,
        "EDIT_STATE_MISMATCH",
        "edit_state",
        explanation,
        observed={
            "active_edit_mode": edit_mode,
            "active_edit_object": _active_sketch_name(state) or None,
            "allowed_edit_modes": sorted(tool.spec.edit_modes),
            "recovery": (
                "Finish or close the active Sketch in the UI, then continue in a new turn."
                if edit_mode == "sketch"
                else "Open the exact target sketch for editing."
            ),
        },
        allowed_values=sorted(tool.spec.edit_modes),
        required_changes=[
            {
                "action": (
                    "human_leave_active_sketch"
                    if edit_mode == "sketch"
                    else "open_target_sketch"
                )
            }
        ],
    )


def _consume_steering(steering_check: SteeringCheck | None) -> list[str]:
    if steering_check is None:
        return []
    values = steering_check() or []
    return [str(value).strip() for value in values if str(value).strip()]


def _emit(progress_callback: ProgressCallback | None, event: dict[str, Any]) -> None:
    if progress_callback is None:
        return
    progress_callback(event)


_TRACE_ITEM_LIMIT = 32
_TRACE_STRING_LIMIT = 1400
_TRACE_DEPTH_LIMIT = 6


def _bounded_trace_value(
    value: Any,
    *,
    path: str,
    depth: int,
    truncated: list[dict[str, Any]],
) -> Any:
    if depth >= _TRACE_DEPTH_LIMIT:
        truncated.append({"path": path, "reason": "depth", "limit": _TRACE_DEPTH_LIMIT})
        return "<truncated>"
    if isinstance(value, str):
        if len(value) <= _TRACE_STRING_LIMIT:
            return value
        truncated.append(
            {
                "path": path,
                "reason": "string_length",
                "original": len(value),
                "limit": _TRACE_STRING_LIMIT,
            }
        )
        return value[: _TRACE_STRING_LIMIT - 3] + "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > _TRACE_ITEM_LIMIT:
            truncated.append(
                {
                    "path": path,
                    "reason": "mapping_items",
                    "original": len(items),
                    "limit": _TRACE_ITEM_LIMIT,
                }
            )
            items = items[:_TRACE_ITEM_LIMIT]
        return {
            str(key): _bounded_trace_value(
                item,
                path=f"{path}.{key}" if path else str(key),
                depth=depth + 1,
                truncated=truncated,
            )
            for key, item in items
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        if len(items) > _TRACE_ITEM_LIMIT:
            truncated.append(
                {
                    "path": path,
                    "reason": "sequence_items",
                    "original": len(items),
                    "limit": _TRACE_ITEM_LIMIT,
                }
            )
            items = items[:_TRACE_ITEM_LIMIT]
        return [
            _bounded_trace_value(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                truncated=truncated,
            )
            for index, item in enumerate(items)
        ]
    return _bounded_trace_value(
        repr(value), path=path, depth=depth, truncated=truncated
    )


def _trace_result(payload: dict[str, Any]) -> dict[str, Any]:
    selected = {
        key: value for key, value in payload.items() if value not in (None, "", [], {})
    }
    selected["ok"] = bool(payload.get("ok"))
    truncated: list[dict[str, Any]] = []
    result = _bounded_trace_value(
        selected,
        path="result",
        depth=0,
        truncated=truncated,
    )
    if truncated:
        result["truncation"] = {
            "truncated": True,
            "entries": truncated[:_TRACE_ITEM_LIMIT],
            "entry_count": len(truncated),
        }
    return result


def _run_domain_vibescript_tool(
    service: VibeCADService,
    tool_name: str,
    args: dict[str, Any],
    *,
    document_thread_dispatch: DocumentThreadDispatch | None,
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
    allow_unchanged_revision: bool = False,
) -> dict[str, Any]:
    """Run one schema-v2 domain lifecycle without blocking the document thread."""

    from VibeCADVibeScriptDomainRuntime import (
        DomainRuntimeFailure,
        accept_candidate,
        abandon_prepared_candidate,
        capture_inspection_state,
        capture_operation_state,
        capture_reference_inputs,
        complete_inspection,
        describe_api,
        finalize_candidate,
        finish_delete,
        parse_domain_tool,
        prepare_candidate,
        prepare_delete,
        restore_prepared_delete,
        retain_candidate,
        _worker_progress,
    )

    lifecycle_started = time.monotonic()
    phase_timings: dict[str, float] = {}

    def run_phase(phase: str, callback: Callable[[], Any]) -> Any:
        _emit(
            progress_callback,
            {
                "event": "vibescript_domain_phase_started",
                "tool_name": tool_name,
                "phase": phase,
            },
        )
        started = time.monotonic()
        completed = False
        try:
            value = callback()
            completed = True
            return value
        finally:
            elapsed = round(time.monotonic() - started, 4)
            phase_timings[phase] = elapsed
            _emit(
                progress_callback,
                {
                    "event": "vibescript_domain_phase_completed",
                    "tool_name": tool_name,
                    "phase": phase,
                    "elapsed_seconds": elapsed,
                    "ok": completed,
                },
            )

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        payload["phase_timings_seconds"] = dict(phase_timings)
        payload["lifecycle_elapsed_seconds"] = round(
            time.monotonic() - lifecycle_started,
            4,
        )
        return payload

    def run_worker_with_progress(
        adapter: Any,
        prepared: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute one worker while publishing changed crash-safe progress."""

        stopped = threading.Event()
        last_encoded = ""

        def publish_current() -> None:
            nonlocal last_encoded
            progress = _worker_progress(prepared)
            if not isinstance(progress, Mapping):
                return
            compact = {
                key: progress[key]
                for key in (
                    "schema",
                    "domain",
                    "phase",
                    "current_output",
                    "phase_elapsed_seconds",
                    "elapsed_seconds",
                    "item_progress",
                    "current_graph_node",
                    "last_completed_graph_node",
                    "completed",
                    "failure",
                )
                if key in progress
            }
            timings = progress.get("graph_timings")
            if isinstance(timings, list):
                compact["completed_graph_node_count"] = len(timings) + int(
                    progress.get("graph_timings_omitted") or 0
                )
            encoded = json.dumps(
                compact,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            if encoded == last_encoded:
                return
            last_encoded = encoded
            _emit(
                progress_callback,
                {
                    "event": "vibescript_domain_worker_progress",
                    **compact,
                },
            )

        def poll() -> None:
            while not stopped.wait(0.2):
                publish_current()

        poller = threading.Thread(
            target=poll,
            name="VibeCAD-VibeScript-worker-progress",
            daemon=True,
        )
        poller.start()
        try:
            return adapter.execute_candidate(
                prepared,
                cancellation_check=cancellation_check,
            )
        finally:
            stopped.set()
            poller.join(timeout=1.0)
            publish_current()

    def candidate_model_state(prepared: Mapping[str, Any]) -> dict[str, Any]:
        program_id = str(prepared["program_id"])
        working_revision = str(prepared["revision"])
        accepted_revision = str(prepared.get("accepted_revision_before") or "")
        return {
            "status": "working_candidate_not_accepted",
            "program_id": program_id,
            "source_id": program_id,
            "working_revision": working_revision,
            "accepted_revision": accepted_revision,
            "accepted_live_state_preserved": bool(accepted_revision),
            "next_write_expected_revision": working_revision,
            "read_source_call": {
                "tool": "vibescript.read_source",
                "arguments": {"source_id": program_id},
            },
            "repair_rule": (
                "Read the source when its text or latest revision is uncertain, then "
                "repair the smallest exact cause. Use vibescript.edit_source for code and "
                "include changed inputs, schema, or output declarations in that same call. "
                "Use vibescript.set_inputs only for a value-only patch."
            ),
        }

    def retain_failed_candidate(
        payload: dict[str, Any],
        prepared: Mapping[str, Any],
        *,
        status: str,
    ) -> dict[str, Any]:
        """Persist a failed source and expose its next editable identity directly."""

        retained = retain_candidate(prepared, status=status, failure=payload)
        program_id = str(prepared["program_id"])
        revision = str(prepared["revision"])
        payload.update(
            {
                "program_id": program_id,
                "source_id": program_id,
                "program_name": str(prepared["program_name"]),
                "domain": str(prepared["pack"].domain),
                "workbench": str(prepared["pack"].workbench),
                "working_revision": revision,
                "next_write_expected_revision": revision,
                "failed_candidate": {
                    "program_id": program_id,
                    "revision": revision,
                    "attempt_directory": retained["attempt_directory"],
                    "accepted_revision": prepared["accepted_revision_before"],
                },
                "model_state": candidate_model_state(prepared),
            }
        )
        return finish(payload)

    parsed = parse_domain_tool(tool_name)
    if parsed is None:
        return tool_failure(
            tool_name,
            "UNKNOWN_DOMAIN_TOOL",
            "surface",
            f"Unknown workbench-qualified VibeScript tool: {tool_name}.",
            requested=args,
        )
    pack, operation = parsed
    adapter = vibescript_domains.get_domain_adapter(pack.domain)
    if adapter is None:
        return tool_failure(
            tool_name,
            "DOMAIN_UNAVAILABLE",
            "surface",
            f"The {pack.title} VibeScript adapter is unavailable.",
            requested=args,
        )
    if operation == "describe_api":
        return describe_api(pack)
    try:
        if operation == "inspect_program":
            captured = run_phase(
                "capture",
                lambda: _on_document_thread(
                    document_thread_dispatch,
                    lambda: capture_inspection_state(
                        service, tool_name, str(args["program_id"])
                    ),
                ),
            )
            return finish(complete_inspection(captured))
        captured = run_phase(
            "capture",
            lambda: _on_document_thread(
                document_thread_dispatch,
                lambda: capture_operation_state(service, tool_name, args),
            ),
        )
        if allow_unchanged_revision:
            captured["allow_unchanged_revision"] = True
        if operation == "delete_program":
            prepared_delete = prepare_delete(captured)
            try:
                publication = _on_document_thread(
                    document_thread_dispatch,
                    lambda: adapter.delete(
                        service,
                        prepared_delete,
                        dict(prepared_delete["manifest"]),
                    ),
                )
            except Exception:
                restore_prepared_delete(prepared_delete)
                raise
            return finish_delete(prepared_delete, publication)
        prepared = run_phase("prepare", lambda: prepare_candidate(captured))
        if prepared.get("reference_requirements") and not prepared.get("finalized"):
            try:
                snapshots = run_phase(
                    "capture_references",
                    lambda: _on_document_thread(
                        document_thread_dispatch,
                        lambda: capture_reference_inputs(service, prepared),
                    ),
                )
                prepared = run_phase(
                    "finalize_candidate",
                    lambda: finalize_candidate(prepared, snapshots),
                )
            except Exception:
                abandon_prepared_candidate(prepared)
                raise
        _emit(
            progress_callback,
            {
                "event": "vibescript_domain_worker_started",
                "domain": pack.domain,
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
            },
        )
        execution = run_phase(
            "worker",
            lambda: run_worker_with_progress(adapter, prepared),
        )
        if execution.get("ok") is not True:
            return retain_failed_candidate(execution, prepared, status="failed")
        try:
            validated = run_phase(
                "validate",
                lambda: adapter.validate_result(prepared, execution),
            )
        except DomainRuntimeFailure as exc:
            return retain_failed_candidate(
                exc.payload,
                prepared,
                status="validation_failed",
            )
        except Exception as exc:
            failure = tool_failure(
                tool_name,
                "DOMAIN_RESULT_INVALID",
                "postcondition",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
            return retain_failed_candidate(
                failure,
                prepared,
                status="validation_failed",
            )
        retain_candidate(prepared, status="validated")
        try:
            publication = run_phase(
                "publish",
                lambda: _on_document_thread(
                    document_thread_dispatch,
                    lambda: adapter.publish(service, prepared, validated),
                ),
            )
        except Exception as exc:
            failure = tool_failure(
                tool_name,
                "DOMAIN_PUBLICATION_FAILED",
                "native_call",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
            return retain_failed_candidate(
                failure,
                prepared,
                status="publication_failed",
            )
        deferred_recompute = run_phase(
            "deferred_recompute",
            lambda: _deferred_publication_recompute(
                service,
                publication,
                dispatch=document_thread_dispatch,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            ),
        )
        payload = accept_candidate(prepared, publication)
        payload["source_id"] = str(payload.get("program_id") or prepared["program_id"])
        payload["deferred_recompute"] = deferred_recompute
        _emit(
            progress_callback,
            {
                "event": "vibescript_domain_publication_completed",
                "domain": pack.domain,
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
                "output_count": len(payload.get("outputs") or []),
            },
        )
        return finish(payload)
    except DomainRuntimeFailure as exc:
        return finish(exc.payload)
    except Exception as exc:
        return finish(
            tool_failure(
                tool_name,
                "DOMAIN_LIFECYCLE_FAILED",
                "external_process",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
        )


_SOURCE_LOG_FIELDS = frozenset(
    {
        "log",
        "logs",
        "progress",
        "raw_progress",
        "stderr",
        "stdout",
        "traceback",
    }
)


def _source_diagnostic_value(
    value: Any,
    *,
    include_logs: bool,
    log_tail_lines: int | None,
) -> Any:
    """Copy candidate state while optionally omitting or tailing raw logs."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        omitted_logs: list[str] = []
        for raw_key, item in value.items():
            key = str(raw_key)
            if key == "artifact_directory" and not include_logs:
                continue
            if (
                key == "worker_progress"
                and isinstance(item, Mapping)
                and not include_logs
            ):
                result[key] = {
                    field: item[field]
                    for field in (
                        "schema",
                        "domain",
                        "phase",
                        "current_output",
                        "current_graph_node",
                        "last_completed_graph_node",
                        "elapsed_seconds",
                        "completed",
                        "failure",
                    )
                    if field in item
                }
                timings = item.get("graph_timings")
                if isinstance(timings, list):
                    result[key]["completed_graph_node_count"] = len(timings) + int(
                        item.get("graph_timings_omitted") or 0
                    )
                continue
            if key.casefold() in _SOURCE_LOG_FIELDS:
                if not include_logs:
                    omitted_logs.append(key)
                    continue
                if isinstance(item, str) and log_tail_lines is not None:
                    lines = item.splitlines()
                    result[key] = "\n".join(lines[-log_tail_lines:])
                    if len(lines) > log_tail_lines:
                        result[f"{key}_lines_omitted"] = len(lines) - log_tail_lines
                    continue
            result[key] = _source_diagnostic_value(
                item,
                include_logs=include_logs,
                log_tail_lines=log_tail_lines,
            )
        if omitted_logs:
            result["logs_omitted"] = sorted(omitted_logs)
        return result
    if isinstance(value, list):
        return [
            _source_diagnostic_value(
                item,
                include_logs=include_logs,
                log_tail_lines=log_tail_lines,
            )
            for item in value
        ]
    return value


def _read_source_payload(
    inspected: Mapping[str, Any],
    *,
    line_start: int | None = None,
    line_end: int | None = None,
    include_logs: bool = False,
    log_tail_lines: int | None = None,
) -> dict[str, Any]:
    if inspected.get("ok") is not True:
        return dict(inspected)
    program = inspected.get("program")
    if not isinstance(program, Mapping):
        return tool_failure(
            "vibescript.read_source",
            "SOURCE_READ_FAILED",
            "precondition",
            "The source read did not return a program contract.",
            observed={"result_fields": sorted(str(key) for key in inspected)},
        )
    source_id = str(program.get("program_id") or "")
    revision = str(program.get("working_revision") or "")
    complete_source = str(program.get("source") or "")
    source_lines = complete_source.splitlines(keepends=True)
    total_lines = len(source_lines)
    ranged = line_start is not None or line_end is not None
    start = int(line_start if line_start is not None else 1)
    end = int(line_end if line_end is not None else total_lines)
    if ranged and (
        start < 1
        or end < start
        or (total_lines > 0 and start > total_lines)
        or (total_lines == 0 and start != 1)
    ):
        return tool_failure(
            "vibescript.read_source",
            "SOURCE_RANGE_INVALID",
            "schema",
            "The requested source line range is outside this saved source.",
            requested={"line_start": line_start, "line_end": line_end},
            observed={"total_lines": total_lines},
        )
    if ranged:
        end = min(end, total_lines)
        returned_source = "".join(source_lines[start - 1 : end])
    else:
        returned_source = complete_source
    expected_output_names = {
        str(item.get("name") or "")
        for item in list(program.get("expected_outputs") or [])
        if isinstance(item, Mapping) and str(item.get("name") or "")
    }

    def output_is_visible(value: Mapping[str, Any], name: str = "") -> bool:
        if include_logs:
            return True
        output_name = str(value.get("name") or name)
        if expected_output_names:
            return output_name in expected_output_names
        return value.get("internal") is not True

    def compact_output(value: Mapping[str, Any], name: str = "") -> dict[str, Any]:
        output_name = str(value.get("name") or name)
        result = {
            key: item
            for key, item in {
                "name": output_name,
                "label": str(value.get("label") or ""),
                "output_type": str(value.get("output_type") or ""),
                "visible": value.get("visible"),
                "derived_state": str(value.get("derived_state") or ""),
                "stale_reason": str(value.get("stale_reason") or ""),
                "reference": value.get("reference"),
            }.items()
            if item not in (None, "", [], {})
        }
        validation_scope = None
        assembly_data = value.get("assembly_data")
        if isinstance(assembly_data, Mapping):
            validation_scope = assembly_data.get("validation_scope")
        accepted_state = value.get("accepted_state")
        if validation_scope is None and isinstance(accepted_state, Mapping):
            validation = accepted_state.get("validation")
            if isinstance(validation, Mapping):
                validation_scope = validation.get("validation_scope")
        if isinstance(validation_scope, Mapping):
            result["validation_scope"] = dict(validation_scope)
        if include_logs:
            for key in ("object_name", "type_id", "source_revision", "internal"):
                item = value.get(key)
                if item not in (None, "", [], {}):
                    result[key] = item
        return result

    raw_outputs = program.get("live_outputs")
    affected_outputs = []
    live_state = program.get("live_state")
    if isinstance(live_state, Mapping) and isinstance(live_state.get("outputs"), list):
        affected_outputs = [
            compact_output(value)
            for value in live_state["outputs"]
            if isinstance(value, Mapping)
            and str(value.get("name") or "")
            and str(value.get("object_name") or "")
            and output_is_visible(value)
        ]
    elif isinstance(raw_outputs, Mapping):
        affected_outputs = [
            compact_output(value, str(name))
            for name, value in sorted(
                raw_outputs.items(),
                key=lambda item: str(item[0]),
            )
            if isinstance(value, Mapping)
            and output_is_visible(value, str(name))
        ]
    result = {
        "ok": True,
        "source_id": source_id,
        "program_id": source_id,
        "current_revision": revision,
        "source": returned_source,
        "source_range": {
            "line_start": start,
            "line_end": end,
            "total_lines": total_lines,
            "complete": not ranged,
        },
        "domain": str(program.get("domain") or ""),
        "workbench": str(program.get("workbench") or ""),
        "label": str(program.get("label") or ""),
        "input_schema": dict(program.get("input_schema") or {}),
        "inputs": dict(program.get("inputs") or {}),
        "expected_outputs": list(program.get("expected_outputs") or []),
        "affected_outputs": affected_outputs,
        "accepted_revision": str(program.get("accepted_revision") or ""),
        "edit_source": {
            "tool": "vibescript.edit_source",
            "target_arguments": {
                "source_id": source_id,
                "expected_revision": revision,
            },
            "source_argument": (
                "Pass the complete updated source text. Read the complete source first; "
                "a line-range response cannot be edited by itself."
            ),
        },
        "build_program": {
            "tool": "vibescript.build_program",
            "arguments": {
                "source_id": source_id,
                "expected_revision": revision,
            },
        },
        "_vibecad_complete_source_result": not ranged,
    }
    latest_candidate = program.get("latest_candidate")
    if isinstance(latest_candidate, Mapping):
        candidate = {
            key: latest_candidate[key]
            for key in ("status", "revision")
            if latest_candidate.get(key) not in (None, "")
        }
        failure = latest_candidate.get("failure")
        if isinstance(failure, Mapping):
            candidate["failure"] = _source_diagnostic_value(
                {
                    key: failure[key]
                    for key in (
                        "failure_code",
                        "failure_stage",
                        "error",
                        "observed",
                    )
                    if failure.get(key) not in (None, "", [], {})
                },
                include_logs=include_logs,
                log_tail_lines=log_tail_lines,
            )
        if candidate:
            result["latest_candidate"] = candidate
    for key in ("migration_required", "migration_reason", "migration_action"):
        if program.get(key) not in (None, "", [], {}):
            result[key] = _source_diagnostic_value(
                program[key],
                include_logs=include_logs,
                log_tail_lines=log_tail_lines,
            )
    model_state = inspected.get("model_state")
    if isinstance(model_state, Mapping):
        compact_state = {
            key: model_state[key]
            for key in (
                "status",
                "candidate_status",
                "accepted_is_current",
                "accepted_live_state_preserved",
            )
            if model_state.get(key) not in (None, "", [], {})
        }
        if compact_state:
            result["model_state"] = compact_state
    return result


def _read_source_index_payload(
    editable_sources: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a compact, human-readable program inventory without internal ids."""

    if not isinstance(editable_sources, Mapping):
        return {
            "ok": True,
            "program_count": 0,
            "programs": [],
            "usage": "Create a program, or open the document that owns it.",
        }
    raw_sources = list(
        editable_sources.get("all_sources")
        or editable_sources.get("sources")
        or []
    )
    programs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source in raw_sources:
        if not isinstance(source, Mapping):
            continue
        program = source.get("program")
        if not isinstance(program, str):
            continue
        key = _program_reference_key(program)
        if not all(key) or key in seen:
            continue
        seen.add(key)
        programs.append(
            {
                "program": program,
                "status": str(source.get("status") or ""),
                "outputs": [
                    {
                        name: value
                        for name, value in dict(output).items()
                        if name in {"name", "label", "object_name", "type_id", "visible"}
                    }
                    for output in list(source.get("affected_outputs") or [])
                    if isinstance(output, Mapping)
                ],
                "read_source": {
                    "tool": "vibescript.read_source",
                    "arguments": {
                        "program": program,
                        "include_logs": False,
                    },
                },
            }
        )
    programs.sort(
        key=lambda item: _program_reference_key(item["program"])
    )
    return {
        "ok": True,
        "program_count": len(programs),
        "programs": programs,
        "usage": (
            "Choose the program by document, domain, and name, then copy its "
            "read_source action. No persistent internal id is needed."
        ),
    }


def _filtered_api_payload(
    tool_name: str,
    description: Mapping[str, Any],
    *,
    names: list[str],
    groups: list[str],
) -> dict[str, Any]:
    """Return either the complete API or a small exact callable selection."""

    names = [name[4:] if name.startswith("api.") else name for name in names]
    result = dict(description)
    exports = [
        dict(item)
        for item in list(result.get("runtime_exports") or [])
        if isinstance(item, Mapping)
    ]
    by_name = {str(item.get("name") or ""): item for item in exports}
    api_groups = {
        str(group): [str(name) for name in raw_names]
        for group, raw_names in dict(result.get("api_groups") or {}).items()
        if isinstance(raw_names, list)
    }
    unknown_names = sorted(set(names) - set(by_name))
    unknown_groups = sorted(set(groups) - set(api_groups))
    if unknown_names or unknown_groups:
        active_workbench = str(result.get("workbench") or "")
        alternate_surfaces = []
        for candidate in vibescript_domains.VIBESCRIPT_WORKBENCH_PACKS.values():
            if candidate.workbench == active_workbench:
                continue
            candidate_names = set(candidate.api_exports)
            candidate_groups = set(vibescript_domains.api_groups(candidate))
            matched_names = sorted(set(unknown_names).intersection(candidate_names))
            matched_groups = sorted(
                set(unknown_groups).intersection(candidate_groups)
            )
            if matched_names or matched_groups:
                alternate_surfaces.append(
                    {
                        "domain": candidate.domain,
                        "workbench": candidate.workbench,
                        "matching_names": matched_names,
                        "matching_groups": matched_groups,
                    }
                )
        complete_surfaces = [
            candidate
            for candidate in alternate_surfaces
            if set(candidate["matching_names"]) == set(unknown_names)
            and set(candidate["matching_groups"]) == set(unknown_groups)
        ]
        if complete_surfaces:
            alternate_surfaces = complete_surfaces
        required_changes = []
        if len(alternate_surfaces) == 1:
            alternate = alternate_surfaces[0]
            required_changes.append(
                "Switch to "
                f"{alternate['workbench']} and retry this read unchanged."
            )
        return tool_failure(
            tool_name,
            "API_FILTER_UNKNOWN",
            "schema",
            "The requested API names or groups do not exist in the active workbench.",
            requested={"names": names, "groups": groups},
            observed={
                "unknown_names": unknown_names,
                "unknown_groups": unknown_groups,
                "available_names": list(by_name),
                "available_groups": list(api_groups),
            },
            candidates=alternate_surfaces,
            required_changes=required_changes,
        )
    if not names and not groups:
        result["_vibecad_complete_api_result"] = True
        return result
    requested_order = list(names)
    for group in groups:
        requested_order.extend(api_groups[group])
    if str(result.get("domain") or "") == "assembly":
        requested_order.extend(("assembly", "solve"))
    ordered_names = list(dict.fromkeys(requested_order))
    focused = {
        key: result[key]
        for key in (
            "ok",
            "domain",
            "workbench",
        )
        if key in result
    }
    focused.update(
        {
            "runtime_exports": [by_name[name] for name in ordered_names],
            "_vibecad_complete_api_result": False,
        }
    )
    details = result.get("api_details")
    if isinstance(details, Mapping):
        selected_details = {
            name: details[name] for name in ordered_names if name in details
        }
        if selected_details:
            focused["api_details"] = selected_details
    return focused


@dataclass(frozen=True)
class _VibeScriptSourceTarget:
    """Exact open document and domain that own one editable source."""

    source_id: str
    pack: Any
    document: Any
    document_uid: str
    document_name: str
    document_path: str
    program_name: str
    current_revision: str
    output_names: tuple[str, ...]


def _program_reference(
    *,
    document_name: str,
    domain: str,
    program_name: str,
) -> str:
    """Return the exact provider-facing identity of one editable program."""

    return "/".join(
        (
            str(document_name or ""),
            str(domain or ""),
            str(program_name or ""),
        )
    )


def _program_reference_key(value: Any) -> tuple[str, str, str]:
    parts = str(value or "").split("/", 2)
    if len(parts) != 3:
        return "", "", ""
    return parts[0].strip(), parts[1].strip().lower(), parts[2].strip()


class _SourceTargetError(RuntimeError):
    def __init__(self, code: str, message: str, *, observed: Mapping[str, Any]):
        self.code = str(code)
        self.observed = dict(observed)
        super().__init__(message)


class _SourceBoundService:
    """Present one source's document/domain to the existing domain lifecycle.

    The underlying service remains the live GUI service.  Only the three inputs
    that define a VibeScript lifecycle target are rebound, so publication uses
    the existing validated transaction and rollback implementation without
    changing the user's active document or ribbon.
    """

    def __init__(self, service: VibeCADService, target: _VibeScriptSourceTarget):
        self._service = service
        self._target = target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    def _active_document(self) -> Any:
        return self._target.document

    def active_workbench_name(self) -> str:
        return str(self._target.pack.workbench)

    def provider_document_revision(
        self,
        native_diagnostics: dict[str, Any] | None = None,
        *,
        object_count: int | None = None,
    ) -> str:
        exact = getattr(self._service, "provider_document_revision_for", None)
        if callable(exact):
            return str(
                exact(
                    self._target.document,
                    native_diagnostics=native_diagnostics,
                    object_count=object_count,
                )
            )
        if self._target.document is self._service._active_document():
            try:
                return str(
                    self._service.provider_document_revision(
                        native_diagnostics,
                        object_count=object_count,
                    )
                )
            except TypeError:
                return str(self._service.provider_document_revision())
        raise RuntimeError(
            "The VibeCAD service cannot calculate a revision for the referenced "
            "source document."
        )


class _DomainBoundService:
    """Bind a new source to one domain without changing the visible ribbon."""

    def __init__(self, service: VibeCADService, pack: Any):
        self._service = service
        self._pack = pack

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    def _active_document(self) -> Any:
        return self._service._active_document()

    def active_workbench_name(self) -> str:
        return str(self._pack.workbench)


def _catalog_source_records(
    component_catalog: Mapping[str, Any] | None,
    source_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(component_catalog, Mapping):
        return []
    records: list[dict[str, Any]] = []
    for candidate in list(component_catalog.get("candidates") or []):
        if not isinstance(candidate, Mapping):
            continue
        authoring = candidate.get("authoring_source")
        if not isinstance(authoring, Mapping):
            continue
        if str(authoring.get("source_id") or "").strip().lower() != source_id:
            continue
        records.append(
            {
                "source": str(candidate.get("source") or ""),
                "reference": dict(candidate.get("reference") or {}),
                "authoring_source": dict(authoring),
                "label": str(candidate.get("label") or ""),
            }
        )
    return records


def _catalog_program_records(
    component_catalog: Mapping[str, Any] | None,
    program: str,
) -> list[dict[str, Any]]:
    """Return exact catalog records for one provider-facing program reference."""

    if not isinstance(component_catalog, Mapping):
        return []
    requested = _program_reference_key(program)
    records: list[dict[str, Any]] = []
    for candidate in list(component_catalog.get("candidates") or []):
        if not isinstance(candidate, Mapping):
            continue
        authoring = candidate.get("authoring_source")
        if not isinstance(authoring, Mapping):
            continue
        candidate_program = authoring.get("program")
        if not isinstance(candidate_program, str):
            candidate_program = _program_reference(
                document_name=str(authoring.get("document_name") or ""),
                domain=str(authoring.get("domain") or ""),
                program_name=str(authoring.get("program_name") or ""),
            )
        if _program_reference_key(candidate_program) != requested:
            continue
        records.append(
            {
                "source": str(candidate.get("source") or ""),
                "reference": dict(candidate.get("reference") or {}),
                "authoring_source": dict(authoring),
                "label": str(candidate.get("label") or ""),
            }
        )
    return records


def _editable_source_record(
    editable_sources: Mapping[str, Any] | None,
    source_id: str,
) -> dict[str, Any] | None:
    if not isinstance(editable_sources, Mapping):
        return None
    raw_sources = list(editable_sources.get("sources") or [])
    raw_sources.extend(list(editable_sources.get("all_sources") or []))
    matches = [
        dict(candidate)
        for candidate in raw_sources
        if isinstance(candidate, Mapping)
        and str(candidate.get("source_id") or "").strip().lower() == source_id
    ]
    matches = list(
        {
            (
                str(candidate.get("domain") or ""),
                str(candidate.get("source_id") or ""),
            ): candidate
            for candidate in matches
        }.values()
    )
    if len(matches) > 1:
        raise _SourceTargetError(
            "SOURCE_OWNERSHIP_CONFLICT",
            f"Source {source_id} appears more than once in the active source index.",
            observed={"source_id": source_id, "source_count": len(matches)},
        )
    return matches[0] if matches else None


def _editable_program_record(
    editable_sources: Mapping[str, Any] | None,
    program: str,
) -> dict[str, Any] | None:
    if not isinstance(editable_sources, Mapping):
        return None
    requested = _program_reference_key(program)
    raw_sources = list(editable_sources.get("sources") or [])
    raw_sources.extend(list(editable_sources.get("all_sources") or []))
    matches: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in raw_sources:
        if not isinstance(candidate, Mapping):
            continue
        candidate_program = candidate.get("program")
        if not isinstance(candidate_program, str):
            continue
        if _program_reference_key(candidate_program) != requested:
            continue
        key = (
            str(candidate.get("domain") or ""),
            str(candidate.get("source_id") or ""),
        )
        matches[key] = dict(candidate)
    if len(matches) > 1:
        raise _SourceTargetError(
            "PROGRAM_REFERENCE_AMBIGUOUS",
            "The program reference matches more than one editable program.",
            observed={"program": program, "match_count": len(matches)},
        )
    return next(iter(matches.values())) if matches else None


def _resolve_vibescript_program_target(
    service: VibeCADService,
    active_pack: Any,
    program: str,
    component_catalog: Mapping[str, Any] | None,
    editable_sources: Mapping[str, Any] | None = None,
) -> _VibeScriptSourceTarget:
    """Resolve one readable program reference to its internal persistent id."""

    requested_program = str(program or "").strip()
    if "/" not in requested_program:
        active_document = service._active_document()
        document_name = str(getattr(active_document, "Name", "") or "").strip()
        if not document_name:
            raise _SourceTargetError(
                "PROGRAM_REFERENCE_INVALID",
                "A local program name requires an active document.",
                observed={"program": requested_program},
            )
        program = _program_reference(
            document_name=document_name,
            domain=str(active_pack.domain),
            program_name=requested_program,
        )
    document_name, domain, program_name = _program_reference_key(program)
    if not document_name or not domain or not program_name:
        raise _SourceTargetError(
            "PROGRAM_REFERENCE_INVALID",
            "A program reference requires document, domain, and name.",
            observed={"program": program},
        )
    editable_record = _editable_program_record(editable_sources, program)
    catalog_records = _catalog_program_records(component_catalog, program)
    internal_ids = {
        str(value or "").strip().lower()
        for value in [
            editable_record.get("source_id") if editable_record else "",
            *[
                record["authoring_source"].get("source_id")
                for record in catalog_records
            ],
        ]
        if str(value or "").strip()
    }

    if not internal_ids:
        try:
            import FreeCAD as App

            documents = list(App.listDocuments().values())
        except Exception:
            documents = [service._active_document()]
        for document in documents:
            if document is None or str(getattr(document, "Name", "") or "") != document_name:
                continue
            for obj in list(getattr(document, "Objects", []) or []):
                if (
                    str(getattr(obj, vibescript_domains.PROP_PROGRAM_DOMAIN, "") or "")
                    .strip()
                    .lower()
                    != domain
                    or str(
                        getattr(obj, vibescript_domains.PROP_PROGRAM_LABEL, "") or ""
                    ).strip()
                    != program_name
                ):
                    continue
                internal_id = str(
                    getattr(obj, vibescript_domains.PROP_PROGRAM_ID, "") or ""
                ).strip().lower()
                if internal_id:
                    internal_ids.add(internal_id)

    if len(internal_ids) != 1:
        raise _SourceTargetError(
            "PROGRAM_NOT_FOUND" if not internal_ids else "PROGRAM_REFERENCE_AMBIGUOUS",
            (
                "No editable program matches this reference."
                if not internal_ids
                else "The program reference resolves to multiple persistent programs."
            ),
            observed={
                "program": program,
                "match_count": len(internal_ids),
            },
        )
    target = _resolve_vibescript_source_target(
        service,
        active_pack,
        next(iter(internal_ids)),
        component_catalog,
        editable_sources,
    )
    actual = _program_reference(
        document_name=target.document_name,
        domain=target.pack.domain,
        program_name=target.program_name,
    )
    if _program_reference_key(actual) != (document_name, domain, program_name):
        raise _SourceTargetError(
            "PROGRAM_REFERENCE_STALE",
            "The program was renamed or moved after this reference was read.",
            observed={"requested": program, "current": actual},
        )
    return target


def _resolve_vibescript_source_target(
    service: VibeCADService,
    active_pack: Any,
    source_id: str,
    component_catalog: Mapping[str, Any] | None,
    editable_sources: Mapping[str, Any] | None = None,
) -> _VibeScriptSourceTarget:
    """Resolve a source only from the active document or the frozen catalog."""

    clean_source_id = str(source_id or "").strip().lower()
    records = _catalog_source_records(component_catalog, clean_source_id)
    editable_record = _editable_source_record(editable_sources, clean_source_id)
    active_document = service._active_document()
    active_uid = str(getattr(active_document, "Uid", "") or "")
    authorized_uids = {active_uid} if active_uid else set()
    expected_domains: dict[str, set[str]] = {}
    for record in records:
        uid = str(record["reference"].get("document_uid") or "")
        if uid:
            authorized_uids.add(uid)
            domain = str(record["authoring_source"].get("domain") or "").strip().lower()
            if domain:
                expected_domains.setdefault(uid, set()).add(domain)

    try:
        import FreeCAD as App

        open_documents = list(App.listDocuments().values())
    except Exception:
        open_documents = [active_document] if active_document is not None else []

    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for document in open_documents:
        if document is None:
            continue
        document_uid = str(getattr(document, "Uid", "") or "")
        if document_uid not in authorized_uids:
            continue
        for obj in list(getattr(document, "Objects", []) or []):
            if str(
                getattr(obj, vibescript_domains.PROP_PROGRAM_ID, "") or ""
            ).strip().lower() != clean_source_id:
                continue
            domain = str(
                getattr(obj, vibescript_domains.PROP_PROGRAM_DOMAIN, "") or ""
            ).strip().lower()
            if (
                expected_domains.get(document_uid)
                and domain not in expected_domains[document_uid]
            ):
                raise _SourceTargetError(
                    "SOURCE_DOMAIN_MISMATCH",
                    f"Source {clean_source_id} does not match its catalog domain.",
                    observed={
                        "source_id": clean_source_id,
                        "document_uid": document_uid,
                        "catalog_domains": sorted(expected_domains[document_uid]),
                        "live_domain": domain,
                    },
                )
            pack = vibescript_domains.get_vibescript_pack_for_domain(domain)
            if pack is None:
                raise _SourceTargetError(
                    "SOURCE_DOMAIN_MISMATCH",
                    f"Source {clean_source_id} declares unsupported domain {domain!r}.",
                    observed={"source_id": clean_source_id, "domain": domain},
                )
            key = (document_uid, domain)
            candidate = candidates.setdefault(
                key,
                {
                    "document": document,
                    "pack": pack,
                    "labels": set(),
                    "revisions": set(),
                    "outputs": set(),
                },
            )
            label = str(
                getattr(obj, vibescript_domains.PROP_PROGRAM_LABEL, "") or ""
            ).strip()
            revision = str(
                getattr(obj, vibescript_domains.PROP_PROGRAM_REVISION, "") or ""
            ).strip().lower()
            output_name = str(
                getattr(obj, vibescript_domains.PROP_PROGRAM_OUTPUT, "") or ""
            ).strip()
            if revision:
                candidate["revisions"].add(revision)
            if label:
                candidate["labels"].add(label)
            if output_name:
                candidate["outputs"].add(output_name)

    if len(candidates) > 1:
        raise _SourceTargetError(
            "SOURCE_OWNERSHIP_CONFLICT",
            f"Source {clean_source_id} is claimed by multiple open documents or domains.",
            observed={
                "source_id": clean_source_id,
                "owners": [
                    {"document_uid": uid, "domain": domain}
                    for uid, domain in sorted(candidates)
                ],
            },
        )
    if candidates:
        (document_uid, _domain), candidate = next(iter(candidates.items()))
        labels = sorted(candidate["labels"])
        if len(labels) != 1:
            raise _SourceTargetError(
                "SOURCE_NAME_CONFLICT",
                "The editable program has no single human-readable name.",
                observed={"program_names": labels},
            )
        revisions = sorted(candidate["revisions"])
        if len(revisions) > 1:
            raise _SourceTargetError(
                "SOURCE_REVISION_CONFLICT",
                f"Source {clean_source_id} has conflicting live output revisions.",
                observed={"source_id": clean_source_id, "revisions": revisions},
            )
        document = candidate["document"]
        return _VibeScriptSourceTarget(
            source_id=clean_source_id,
            pack=candidate["pack"],
            document=document,
            document_uid=document_uid,
            document_name=str(getattr(document, "Name", "") or ""),
            document_path=str(getattr(document, "FileName", "") or ""),
            program_name=labels[0],
            current_revision=revisions[0] if revisions else "",
            output_names=tuple(sorted(candidate["outputs"])),
        )

    if editable_record is not None:
        record_domain = str(editable_record.get("domain") or "").strip().lower()
        index_domain = (
            str(editable_sources.get("domain") or "").strip().lower()
            if isinstance(editable_sources, Mapping)
            else ""
        )
        authoring_domains = {
            str(value or "").strip().lower()
            for value in list(editable_sources.get("authoring_domains") or [])
        }
        if (
            record_domain
            and index_domain
            and record_domain != index_domain
            and not {record_domain, index_domain} <= authoring_domains
        ):
            raise _SourceTargetError(
                "SOURCE_DOMAIN_MISMATCH",
                f"Source {clean_source_id} conflicts with its owning source index.",
                observed={
                    "source_id": clean_source_id,
                    "record_domain": record_domain,
                    "index_domain": index_domain,
                },
            )
        indexed_domain = record_domain or index_domain
        if not indexed_domain and isinstance(editable_sources, Mapping):
            indexed_workbench = str(editable_sources.get("workbench") or "").strip()
            indexed_pack = vibescript_domains.get_vibescript_pack(indexed_workbench)
            if indexed_pack is not None:
                indexed_domain = indexed_pack.domain
        pack = vibescript_domains.get_vibescript_pack_for_domain(indexed_domain)
        if pack is None or (
            pack.domain != active_pack.domain
            and pack.domain not in authoring_domains
        ):
            raise _SourceTargetError(
                "SOURCE_DOMAIN_MISMATCH",
                f"Source {clean_source_id} does not belong to the active source domain.",
                observed={
                    "source_id": clean_source_id,
                    "active_domain": str(active_pack.domain),
                    "indexed_domain": indexed_domain,
                },
            )
        if active_document is None:
            raise _SourceTargetError(
                "SOURCE_DOCUMENT_NOT_OPEN",
                "The source's owning document is not open.",
                observed={"source_id": clean_source_id},
            )
        return _VibeScriptSourceTarget(
            source_id=clean_source_id,
            pack=pack,
            document=active_document,
            document_uid=active_uid,
            document_name=str(getattr(active_document, "Name", "") or ""),
            document_path=str(getattr(active_document, "FileName", "") or ""),
            program_name=str(editable_record.get("label") or ""),
            current_revision=str(
                editable_record.get("current_revision") or ""
            ).strip(),
            output_names=tuple(
                sorted(
                    str(item.get("name") or "")
                    for item in list(editable_record.get("affected_outputs") or [])
                    if isinstance(item, Mapping) and str(item.get("name") or "")
                )
            ),
        )

    if records:
        references = [dict(record["reference"]) for record in records]
        raise _SourceTargetError(
            "SOURCE_DOCUMENT_NOT_OPEN",
            "The component's authoring document is not open. Open it, then retry "
            "the same source operation.",
            observed={"source_id": clean_source_id, "documents": references},
        )
    raise _SourceTargetError(
        "SOURCE_NOT_FOUND",
        f"Source {clean_source_id} is not available in an authorized open document.",
        observed={"source_id": clean_source_id},
    )


def _source_target_payload(target: _VibeScriptSourceTarget) -> dict[str, Any]:
    return {
        "program": _program_reference(
            document_name=target.document_name,
            domain=target.pack.domain,
            program_name=target.program_name,
        ),
        "workbench": str(target.pack.workbench),
        "document_path": target.document_path,
        "current_revision": target.current_revision,
        "affected_outputs": list(target.output_names),
    }


def _run_universal_vibescript_tool(
    service: VibeCADService,
    active_workbench: str | None,
    tool_name: str,
    args: dict[str, Any],
    *,
    component_catalog: Mapping[str, Any] | None = None,
    editable_sources: Mapping[str, Any] | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None,
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    args = dict(args)
    active_pack = vibescript_domains.get_vibescript_pack(active_workbench)
    if active_pack is None:
        return tool_failure(
            tool_name,
            "DOMAIN_UNAVAILABLE",
            "surface",
            "The active workbench has no VibeScript source domain.",
            requested=args,
        )
    target: _VibeScriptSourceTarget | None = None
    pack = active_pack
    replacement: dict[str, Any] | None = None
    if tool_name == "vibescript.create_assembly" and "replace" in args:
        raw_replacement = args.pop("replace")
        if not isinstance(raw_replacement, Mapping):
            return tool_failure(
                tool_name,
                "ASSEMBLY_REPLACEMENT_INVALID",
                "schema",
                "replace must contain program and expected_revision.",
                requested=args,
            )
        replacement = dict(raw_replacement)
        args["program"] = replacement.get("program")
    program = args.get("program")
    requested_domain = str(args.get("domain") or "").strip().lower()
    if program is not None and not isinstance(program, str):
        return tool_failure(
            tool_name,
            "PROGRAM_REFERENCE_INVALID",
            "schema",
            "program must be the exact document/domain/name reference returned by read_source.",
            requested=args,
        )
    if isinstance(program, str) and requested_domain:
        return tool_failure(
            tool_name,
            "SOURCE_TARGET_AMBIGUOUS",
            "schema",
            "Pass program for an existing source or domain for a new source/API, not both.",
            requested=args,
            required_changes=[{"remove_one_of": ["program", "domain"]}],
        )
    if isinstance(program, str):
        try:
            target = _on_document_thread(
                document_thread_dispatch,
                lambda: _resolve_vibescript_program_target(
                    service,
                    active_pack,
                    program,
                    component_catalog,
                    editable_sources,
                ),
            )
        except _SourceTargetError as exc:
            if (
                tool_name == "vibescript.read_source"
                and exc.code == "PROGRAM_NOT_FOUND"
                and "/" not in program
            ):
                return {
                    "ok": True,
                    "found": False,
                    "program_name": program.strip(),
                }
            return tool_failure(
                tool_name,
                exc.code,
                "precondition",
                str(exc),
                requested=args,
                observed=exc.observed,
            )
        pack = target.pack
    elif requested_domain:
        requested_pack = vibescript_domains.get_vibescript_pack_for_domain(
            requested_domain
        )
        if requested_pack is None:
            return tool_failure(
                tool_name,
                "DOMAIN_UNAVAILABLE",
                "surface",
                f"No VibeScript API owns domain {requested_domain!r}.",
                requested=args,
                allowed_values=sorted(
                    {
                        candidate.domain
                        for candidate in vibescript_domains.VIBESCRIPT_WORKBENCH_PACKS.values()
                    }
                ),
            )
        available, reason = vibescript_domains.domain_availability(
            requested_pack.workbench
        )
        if not available:
            return tool_failure(
                tool_name,
                "DOMAIN_UNAVAILABLE",
                "surface",
                reason,
                requested=args,
            )
        pack = requested_pack
    try:
        if tool_name == "vibescript.create_part":
            requested_domain = "partdesign"
            component_pack = vibescript_domains.get_vibescript_pack_for_domain(
                requested_domain
            )
            if component_pack is None:
                return tool_failure(
                    tool_name,
                    "DOMAIN_UNAVAILABLE",
                    "surface",
                    "The Part Design VibeScript domain is unavailable.",
                    requested=args,
                )
            pack = component_pack
        if tool_name == "vibescript.create_assembly":
            from VibeCADAssemblyGraphProgram import (
                AssemblyGraphProgramError,
                compile_assembly_program,
            )

            try:
                requested_domain = "assembly"
                assembly_pack = vibescript_domains.get_vibescript_pack_for_domain(
                    requested_domain
                )
                if assembly_pack is None:
                    raise AssemblyGraphProgramError(
                        "The Assembly VibeScript domain is unavailable"
                    )
                pack = assembly_pack
                compiled = compile_assembly_program(
                    args,
                    component_catalog=component_catalog,
                )
                if target is None:
                    document = service._active_document()
                    existing_program = _program_reference(
                        document_name=str(getattr(document, "Name", "") or ""),
                        domain="assembly",
                        program_name=str(compiled["program_name"]),
                    )
                    try:
                        existing = _editable_program_record(
                            editable_sources,
                            existing_program,
                        )
                    except _SourceTargetError as exc:
                        return tool_failure(
                            tool_name,
                            exc.code,
                            "precondition",
                            str(exc),
                            requested=args,
                            observed=exc.observed,
                        )
                    existing_revision = str(
                        (existing or {}).get("current_revision") or ""
                    )
                    if existing is not None and existing_revision:
                        replace = {
                            "program": existing_program,
                            "expected_revision": existing_revision,
                        }
                        return tool_failure(
                            tool_name,
                            "ASSEMBLY_REPLACEMENT_REQUIRED",
                            "precondition",
                            "An assembly source with this name already exists.",
                            requested=args,
                            observed={
                                "program": existing_program,
                                "current_revision": existing_revision,
                            },
                            required_changes=[{"replace": replace}],
                            next_actions=[
                                {
                                    "tool": tool_name,
                                    "target_arguments": {"replace": replace},
                                }
                            ],
                        )
                if target is not None and replacement is not None:
                    compiled["expected_revision"] = replacement.get(
                        "expected_revision"
                    )
                args = {
                    "domain": "assembly",
                    **compiled,
                }
            except AssemblyGraphProgramError as exc:
                return tool_failure(
                    tool_name,
                    "ASSEMBLY_GRAPH_INVALID",
                    "schema",
                    str(exc),
                    requested=args,
                    observed={"path": exc.path, **exc.observed},
                    allowed_values=exc.allowed_values,
                    required_changes=(
                        exc.required_changes
                        or (
                            [
                                {
                                    "path": exc.path,
                                    "allowed_values": exc.allowed_values,
                                }
                            ]
                            if exc.path and exc.allowed_values
                            else []
                        )
                    ),
                )
        args = _resolve_component_catalog_inputs(args, component_catalog)
    except _ComponentCatalogInputError as exc:
        return tool_failure(
            tool_name,
            "COMPONENT_CATALOG_KEY_INVALID",
            "schema",
            str(exc),
            requested=args,
            observed={"available_catalog_keys": exc.available_keys},
        )
    if target is not None:
        domain_service: Any = _SourceBoundService(service, target)
    elif requested_domain:
        domain_service = _DomainBoundService(service, pack)
    else:
        domain_service = service

    def finish_source_write(result: dict[str, Any]) -> dict[str, Any]:
        result["_vibecad_source_lifecycle_result"] = True
        persisted_source = target is not None or bool(
            result.get("working_revision")
            or result.get("current_revision")
            or result.get("program_id")
            or result.get("source_id")
        )
        if not persisted_source:
            if (
                tool_name in {
                    "vibescript.create_program",
                    "vibescript.create_assembly",
                    "vibescript.create_part",
                }
                and result.get("ok") is not True
            ):
                result["error"] = (
                    str(result.get("error") or "VibeScript program creation failed.")
                    + " No editable source was saved. Correct the reported request "
                    + f"field(s), then retry {tool_name}."
                )
                result["next_actions"] = [{"tool": tool_name}]
                retry = result.get("retry")
                if not isinstance(retry, dict):
                    retry = {}
                retry["same_call"] = False
                result["retry"] = retry
            return result
        if target is not None:
            target_payload = _source_target_payload(target)
        else:
            document = domain_service._active_document()
            target_payload = {
                "program": _program_reference(
                    document_name=str(getattr(document, "Name", "") or ""),
                    domain=pack.domain,
                    program_name=str(
                        result.get("program_name")
                        or args.get("program_name")
                        or ""
                    ),
                ),
                "workbench": str(pack.workbench),
                "document_path": str(getattr(document, "FileName", "") or ""),
                "current_revision": str(result.get("working_revision") or ""),
                "affected_outputs": sorted(
                    str(name) for name in dict(result.get("live_outputs") or {})
                ),
            }
        if not _program_reference_key(target_payload["program"])[2]:
            return result
        if result.get("working_revision"):
            target_payload["current_revision"] = str(result["working_revision"])
        result["program"] = str(target_payload["program"])
        result["source_target"] = target_payload
        if tool_name == "vibescript.create_assembly" and result.get("ok") is not True:
            revision = str(target_payload.get("current_revision") or "")
            if revision:
                result["next_actions"] = [
                    {
                        "tool": tool_name,
                        "target_arguments": {
                            "replace": {
                                "program": str(target_payload["program"]),
                                "expected_revision": revision,
                            }
                        },
                    }
                ]
        active_document = service._active_document()
        target_document = target.document if target is not None else document
        if result.get("ok") is True and active_document is not target_document:
            try:
                _on_document_thread(
                    document_thread_dispatch,
                    lambda: active_document.recompute()
                    if active_document is not None
                    else None,
                )
                result["referencing_document_refreshed"] = True
            except Exception as exc:
                result.setdefault("warnings", []).append(
                    {
                        "code": "REFERENCING_DOCUMENT_REFRESH_FAILED",
                        "error": str(exc),
                    }
                )
        return result
    if tool_name == "vibescript.read_api":
        from VibeCADVibeScriptDomainRuntime import describe_api

        payload = _filtered_api_payload(
            tool_name,
            describe_api(pack),
            names=[str(value) for value in list(args.get("names") or [])],
            groups=[str(value) for value in list(args.get("groups") or [])],
        )
        if target is not None:
            payload["source_target"] = _source_target_payload(target)
        return payload
    if tool_name == "vibescript.read_geometry":
        from VibeCADGeometryInspection import (
            GeometryInspectionError,
            capture_geometry_read,
            complete_geometry_read,
            discard_geometry_read,
        )

        try:
            captured = _on_document_thread(
                document_thread_dispatch,
                lambda: capture_geometry_read(service, args),
            )
            if cancellation_check is not None and cancellation_check():
                discard_geometry_read(captured)
                return tool_failure(
                    tool_name,
                    "RUN_CANCELLED",
                    "precondition",
                    "The geometry read was superseded after capture.",
                    requested=args,
                    cancelled=True,
                )
            _emit(
                progress_callback,
                {
                    "event": "geometry_worker_started",
                    "operation": "inspect_brep",
                    "analysis_level": str(
                        args.get("analysis_level") or "full"
                    ),
                    "include_subelements": bool(args.get("include_subelements")),
                },
            )
            payload = complete_geometry_read(
                captured,
                cancellation_check=cancellation_check,
            )
            payload["_vibecad_geometry_read_request"] = dict(args)
            return payload
        except GeometryInspectionError as exc:
            return tool_failure(
                tool_name,
                exc.code,
                "precondition",
                str(exc),
                requested=args,
            )
        except Exception as exc:
            return tool_failure(
                tool_name,
                "GEOMETRY_READ_FAILED",
                "native_call",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
    if tool_name == "vibescript.read_placement":
        from VibeCADPlacementInspection import (
            PlacementInspectionError,
            read_placement,
        )

        try:
            return read_placement(args)
        except PlacementInspectionError as exc:
            return tool_failure(
                tool_name,
                exc.code,
                "precondition",
                str(exc),
                requested=args,
            )
        except Exception as exc:
            return tool_failure(
                tool_name,
                "PLACEMENT_READ_FAILED",
                "precondition",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
    if tool_name == "vibescript.read_source":
        from VibeCADVibeScriptDomainRuntime import (
            DomainRuntimeFailure,
            capture_inspection_state,
            complete_inspection,
        )

        if target is None:
            return _read_source_index_payload(editable_sources)
        source_id = target.source_id
        try:
            captured = _on_document_thread(
                document_thread_dispatch,
                lambda: capture_inspection_state(
                    domain_service,
                    f"vibescript.{pack.domain}.inspect_program",
                    source_id,
                ),
            )
            payload = _read_source_payload(
                complete_inspection(captured),
                line_start=args.get("line_start"),
                line_end=args.get("line_end"),
                include_logs=bool(args.get("include_logs", False)),
                log_tail_lines=args.get("log_tail_lines"),
            )
            if target is not None:
                payload["source_target"] = _source_target_payload(target)
            payload["program"] = str(payload["source_target"]["program"])
            payload.pop("source_id", None)
            payload.pop("program_id", None)
            payload["edit_source"] = {
                "tool": "vibescript.edit_source",
                "target_arguments": {
                    "program": str(payload["program"]),
                    "expected_revision": str(payload.get("current_revision") or ""),
                },
                "source_argument": (
                    "Pass the complete updated source text returned by this read."
                ),
            }
            payload["build_program"] = {
                "tool": "vibescript.build_program",
                "arguments": {
                    "program": str(payload["program"]),
                    "expected_revision": str(payload.get("current_revision") or ""),
                },
            }
            payload["_vibecad_source_read_result"] = True
            return payload
        except DomainRuntimeFailure as exc:
            return exc.payload
        except Exception as exc:
            return tool_failure(
                tool_name,
                "SOURCE_READ_FAILED",
                "precondition",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
    if tool_name == "vibescript.delete_output":
        from VibeCADVibeScriptDomainRuntime import (
            DomainRuntimeFailure,
            capture_inspection_state,
            complete_inspection,
        )

        if target is None:
            return tool_failure(
                tool_name,
                "PROGRAM_REFERENCE_REQUIRED",
                "schema",
                "Select the owning program by document, domain, and name.",
                requested=args,
            )
        requested_output = str(args["output_name"])
        try:
            captured = _on_document_thread(
                document_thread_dispatch,
                lambda: capture_inspection_state(
                    domain_service,
                    f"vibescript.{pack.domain}.inspect_program",
                    target.source_id,
                ),
            )
            inspected = complete_inspection(captured)
            if inspected.get("ok") is not True:
                return inspected
            program = inspected.get("program")
            if not isinstance(program, Mapping):
                raise RuntimeError("The saved source has no readable program contract.")
            current_revision = str(program.get("working_revision") or "")
            expected_revision = str(args["expected_revision"])
            if expected_revision != current_revision:
                return tool_failure(
                    tool_name,
                    "STALE_PROGRAM_REVISION",
                    "precondition",
                    "The source changed after it was read.",
                    requested={"expected_revision": expected_revision},
                    observed={"current_revision": current_revision},
                )
            expected_outputs = [
                dict(item) for item in list(program.get("expected_outputs") or [])
            ]
            output_names = [str(item.get("name") or "") for item in expected_outputs]
            if requested_output not in output_names:
                return tool_failure(
                    tool_name,
                    "OUTPUT_NOT_FOUND",
                    "precondition",
                    "The exact output is not declared by this source.",
                    requested={"output_name": requested_output},
                    observed={"available_outputs": output_names},
                )
            if len(expected_outputs) == 1:
                return tool_failure(
                    tool_name,
                    "LAST_OUTPUT_REQUIRES_PROGRAM_DELETE",
                    "precondition",
                    "This is the source's only output; use vibescript.delete_program.",
                    requested={"output_name": requested_output},
                    observed={"available_outputs": output_names},
                )
            remaining_outputs = [
                item
                for item in expected_outputs
                if str(item.get("name") or "") != requested_output
            ]
            domain_args = {
                "program_id": target.source_id,
                "expected_revision": expected_revision,
                "source": str(args["source"]),
                "input_schema": dict(program.get("input_schema") or {}),
                "inputs": dict(program.get("inputs") or {}),
                "expected_outputs": remaining_outputs,
            }
            qualified_name = f"vibescript.{pack.domain}.reconfigure_program"
            result = _run_domain_vibescript_tool(
                domain_service,
                qualified_name,
                domain_args,
                document_thread_dispatch=document_thread_dispatch,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            )
            if result.get("tool") == qualified_name:
                result["tool"] = tool_name
            if result.get("program_id"):
                result["source_id"] = str(result["program_id"])
            if result.get("ok") is True:
                result["deleted_output"] = requested_output
                result["reason"] = str(args["reason"])
            return finish_source_write(result)
        except DomainRuntimeFailure as exc:
            return exc.payload
        except Exception as exc:
            return tool_failure(
                tool_name,
                "OUTPUT_DELETE_FAILED",
                "precondition",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
    universal_domain_operations = {
        "vibescript.create_assembly": "create_program",
        "vibescript.create_part": "create_program",
        "vibescript.create_program": "create_program",
        "vibescript.set_inputs": "set_inputs",
        "vibescript.reconfigure_program": "reconfigure_program",
        "vibescript.delete_program": "delete_program",
    }
    operation = universal_domain_operations.get(tool_name)
    if tool_name == "vibescript.create_assembly" and target is not None:
        operation = "reconfigure_program"
    if operation is not None:
        if operation != "create_program" and target is None:
            return tool_failure(
                tool_name,
                "PROGRAM_REFERENCE_REQUIRED",
                "schema",
                "Select one program by document, domain, and name.",
                requested=args,
            )
        domain_args = dict(args)
        domain_args.pop("domain", None)
        domain_args.pop("program", None)
        if operation == "reconfigure_program":
            domain_args.pop("program_name", None)
        if target is not None:
            domain_args["program_id"] = target.source_id
        qualified_name = f"vibescript.{pack.domain}.{operation}"
        result = _run_domain_vibescript_tool(
            domain_service,
            qualified_name,
            domain_args,
            document_thread_dispatch=document_thread_dispatch,
            cancellation_check=cancellation_check,
            progress_callback=progress_callback,
        )
        if result.get("tool") == qualified_name:
            result["tool"] = tool_name
        if result.get("program_id"):
            result["source_id"] = str(result["program_id"])
        return finish_source_write(result)
    if tool_name == "vibescript.build_program":
        from VibeCADVibeScriptDomainRuntime import (
            DomainRuntimeFailure,
            capture_inspection_state,
            complete_inspection,
        )

        if target is None:
            return tool_failure(
                tool_name,
                "PROGRAM_REFERENCE_REQUIRED",
                "schema",
                "Select one program by document, domain, and name.",
                requested=args,
            )
        source_id = target.source_id
        expected_revision = str(args["expected_revision"])
        try:
            captured = _on_document_thread(
                document_thread_dispatch,
                lambda: capture_inspection_state(
                    domain_service,
                    f"vibescript.{pack.domain}.inspect_program",
                    source_id,
                ),
            )
            inspected = complete_inspection(captured)
            program = inspected.get("program")
            if not isinstance(program, Mapping):
                return tool_failure(
                    tool_name,
                    "SOURCE_READ_FAILED",
                    "precondition",
                    "The saved program could not be read before building.",
                    requested=args,
                )
            current_revision = str(program.get("working_revision") or "")
            if current_revision != expected_revision:
                return tool_failure(
                    tool_name,
                    "STALE_PROGRAM_REVISION",
                    "precondition",
                    "The saved program changed after it was selected for building.",
                    requested={"expected_revision": expected_revision},
                    observed={"current_revision": current_revision},
                    required_changes=[
                        {
                            "tool": "vibescript.read_source",
                            "arguments": {
                                "program": _source_target_payload(target)["program"],
                                "include_logs": False,
                            },
                        }
                    ],
                )
            result = _run_domain_vibescript_tool(
                domain_service,
                f"vibescript.{pack.domain}.edit_source",
                {
                    "program_id": source_id,
                    "expected_revision": expected_revision,
                    "source": str(program.get("source") or ""),
                },
                document_thread_dispatch=document_thread_dispatch,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
                allow_unchanged_revision=True,
            )
            if result.get("tool") == f"vibescript.{pack.domain}.edit_source":
                result["tool"] = tool_name
            if result.get("program_id"):
                result["source_id"] = str(result["program_id"])
            result["requested_action"] = "build_program"
            return finish_source_write(result)
        except DomainRuntimeFailure as exc:
            return exc.payload
        except Exception as exc:
            return tool_failure(
                tool_name,
                "PROGRAM_BUILD_FAILED",
                "external_process",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
    if tool_name == "vibescript.edit_source":
        if target is None:
            return tool_failure(
                tool_name,
                "PROGRAM_REFERENCE_REQUIRED",
                "schema",
                "Select one program by document, domain, and name.",
                requested=args,
            )
        domain_arguments = {
            "program_id": target.source_id,
            "expected_revision": str(args["expected_revision"]),
            "source": str(args["source"]),
        }
        for name in ("input_schema", "inputs", "expected_outputs"):
            if name in args:
                domain_arguments[name] = args[name]
        result = _run_domain_vibescript_tool(
            domain_service,
            f"vibescript.{pack.domain}.edit_source",
            domain_arguments,
            document_thread_dispatch=document_thread_dispatch,
            cancellation_check=cancellation_check,
            progress_callback=progress_callback,
        )
        if result.get("program_id"):
            result["source_id"] = str(result["program_id"])
        return finish_source_write(result)
    return tool_failure(
        tool_name,
        "UNKNOWN_VIBESCRIPT_SOURCE_TOOL",
        "surface",
        f"Unknown universal VibeScript source tool: {tool_name}.",
        requested=args,
    )


def run_domain_vibescript_operation(
    service: VibeCADService,
    tool_name: str,
    args: dict[str, Any],
    *,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Public editor bridge for one workbench-qualified v2 operation."""

    if (
        vibescript_domains.get_domain_adapter(
            tool_name.split(".")[1]
            if tool_name.startswith("vibescript.") and tool_name.count(".") == 2
            else ""
        )
        is None
    ):
        raise ValueError(f"No VibeScript v2 domain adapter owns {tool_name!r}.")
    return _run_domain_vibescript_tool(
        service,
        tool_name,
        dict(args),
        document_thread_dispatch=document_thread_dispatch,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )


def build_domain_vibescript_editor_candidate(
    service: VibeCADService,
    tool_name: str,
    args: dict[str, Any],
    *,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> dict[str, Any]:
    """Build and retain one editor candidate without publishing live objects."""

    from VibeCADVibeScriptDomainRuntime import (
        DomainRuntimeFailure,
        abandon_prepared_candidate,
        capture_operation_state,
        capture_reference_inputs,
        finalize_candidate,
        parse_domain_tool,
        prepare_candidate,
        retain_candidate,
    )

    parsed = parse_domain_tool(tool_name)
    if parsed is None:
        return tool_failure(
            tool_name,
            "UNKNOWN_DOMAIN_TOOL",
            "surface",
            f"Unknown workbench-qualified VibeScript tool: {tool_name}.",
            requested=args,
        )
    pack, operation = parsed
    if operation not in {"edit_source", "set_inputs", "reconfigure_program"}:
        return tool_failure(
            tool_name,
            "EDITOR_OPERATION_UNSUPPORTED",
            "precondition",
            "The editor candidate path accepts only existing-program mutations.",
            requested=args,
        )
    adapter = vibescript_domains.get_domain_adapter(pack.domain)
    if adapter is None:
        return tool_failure(
            tool_name,
            "DOMAIN_UNAVAILABLE",
            "surface",
            f"The {pack.title} VibeScript adapter is unavailable.",
            requested=args,
        )
    prepared = None
    try:
        if cancellation_check is not None and cancellation_check():
            return tool_failure(
                tool_name,
                "RUN_CANCELLED",
                "precondition",
                "The editor build was superseded before capture.",
                requested=args,
                cancelled=True,
            )
        captured = _on_document_thread(
            document_thread_dispatch,
            lambda: capture_operation_state(service, tool_name, args),
        )
        # A human pressing Build is an explicit request to execute the current
        # program, even when its content digest matches the prior revision.
        # Provider mutations keep the unchanged-revision guard.
        captured["allow_unchanged_revision"] = True
        prepared = prepare_candidate(captured)
        if prepared.get("reference_requirements") and not prepared.get("finalized"):
            try:
                snapshots = _on_document_thread(
                    document_thread_dispatch,
                    lambda: capture_reference_inputs(service, prepared),
                )
                prepared = finalize_candidate(prepared, snapshots)
            except Exception:
                abandon_prepared_candidate(prepared)
                raise
        execution = adapter.execute_candidate(
            prepared,
            cancellation_check=cancellation_check,
        )
        if execution.get("ok") is not True:
            retained = retain_candidate(prepared, status="failed", failure=execution)
            execution["failed_candidate"] = {
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
                "attempt_directory": retained["attempt_directory"],
                "accepted_revision": prepared["accepted_revision_before"],
            }
            return execution
        try:
            validated = adapter.validate_result(prepared, execution)
        except DomainRuntimeFailure as exc:
            retained = retain_candidate(
                prepared,
                status="validation_failed",
                failure=exc.payload,
            )
            exc.payload["failed_candidate"] = {
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
                "attempt_directory": retained["attempt_directory"],
                "accepted_revision": prepared["accepted_revision_before"],
            }
            return exc.payload
        except Exception as exc:
            failure = tool_failure(
                tool_name,
                "DOMAIN_RESULT_INVALID",
                "postcondition",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
            retained = retain_candidate(
                prepared,
                status="validation_failed",
                failure=failure,
            )
            failure["failed_candidate"] = {
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
                "attempt_directory": retained["attempt_directory"],
                "accepted_revision": prepared["accepted_revision_before"],
            }
            return failure
        retained = retain_candidate(prepared, status="validated")
        return {
            "ok": True,
            "program_id": str(prepared["program_id"]),
            "program_name": str(prepared["program_name"]),
            "domain": pack.domain,
            "working_revision": str(prepared["revision"]),
            "accepted_revision": str(prepared.get("accepted_revision_before") or ""),
            "attempt_directory": retained["attempt_directory"],
            "output_count": len(validated.get("outputs") or []),
            "stdout": str(validated.get("stdout") or ""),
            "budget": dict(validated.get("budget") or {}),
            "_editor_candidate": {
                "prepared": prepared,
                "validated": validated,
            },
        }
    except DomainRuntimeFailure as exc:
        return exc.payload
    except Exception as exc:
        if prepared is not None:
            try:
                abandon_prepared_candidate(prepared)
            except Exception:
                pass
        return tool_failure(
            tool_name,
            "DOMAIN_EDITOR_BUILD_FAILED",
            "external_process",
            str(exc),
            requested=args,
            observed={"exception_type": exc.__class__.__name__},
        )


def apply_domain_vibescript_editor_candidate(
    service: VibeCADService,
    candidate: Mapping[str, Any],
    *,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> dict[str, Any]:
    """Publish a previously validated editor candidate, then accept its manifest."""

    from VibeCADVibeScriptDomainRuntime import accept_candidate, retain_candidate

    prepared = candidate.get("prepared")
    validated = candidate.get("validated")
    if not isinstance(prepared, Mapping) or not isinstance(validated, Mapping):
        return tool_failure(
            "vibescript.editor.apply",
            "INVALID_EDITOR_CANDIDATE",
            "precondition",
            "The editor has no complete validated candidate to apply.",
        )
    tool_name = str(prepared.get("tool_name") or "vibescript.editor.apply")
    if cancellation_check is not None and cancellation_check():
        return tool_failure(
            tool_name,
            "RUN_CANCELLED",
            "precondition",
            "The editor apply was superseded before publication.",
            cancelled=True,
        )
    adapter = vibescript_domains.get_domain_adapter(prepared["pack"].domain)
    if adapter is None:
        return tool_failure(
            tool_name,
            "DOMAIN_UNAVAILABLE",
            "surface",
            "The candidate's VibeScript domain is no longer available.",
        )
    try:
        publication = _on_document_thread(
            document_thread_dispatch,
            lambda: adapter.publish(service, dict(prepared), dict(validated)),
        )
    except Exception as exc:
        failure = tool_failure(
            tool_name,
            "DOMAIN_PUBLICATION_FAILED",
            "native_call",
            str(exc),
            observed={"exception_type": exc.__class__.__name__},
        )
        retain_candidate(prepared, status="publication_failed", failure=failure)
        return failure
    deferred_recompute = _deferred_publication_recompute(
        service,
        publication,
        dispatch=document_thread_dispatch,
        cancellation_check=cancellation_check,
        progress_callback=None,
    )
    payload = accept_candidate(prepared, publication)
    payload["deferred_recompute"] = deferred_recompute
    return payload


def make_provider_tool_runner(
    service: VibeCADService,
    *,
    tool_trace: list[dict[str, Any]],
    progress_callback: ProgressCallback | None,
    cancellation_check: CancellationCheck | None,
    steering_check: SteeringCheck | None,
    question_callback: QuestionCallback | None,
    output_authorization_callback: NativeOutputAuthorizer | None = None,
    input_authorization_callback: NativeInputAuthorizer | None = None,
    session_trigger: dict[str, Any] | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
    turn_surface: dict[str, Any] | None = None,
    turn_schemas: list[dict[str, Any]] | None = None,
    turn_authorization: Mapping[str, Any] | None = None,
    turn_modeling_surface: dict[str, Any] | None = None,
    turn_component_catalog: Mapping[str, Any] | None = None,
    turn_editable_sources: Mapping[str, Any] | None = None,
    provider_calls_allowed: bool = True,
):
    if (
        isinstance(turn_surface, Mapping)
        and str(turn_surface.get("engine") or "") == "native"
    ):
        from VibeCADNativeProviderRunner import NativeProviderToolRunner
        from VibeCADNativeSessionFactory import create_native_session_execution

        debug_config = service.provider_debug_config()
        debug_events: list[dict[str, Any]] | None = (
            [] if debug_config.get("enabled") else None
        )
        execution = _on_document_thread(
            document_thread_dispatch,
            lambda: create_native_session_execution(
                service=service,
                expected_surface=dict(turn_surface),
                expected_schemas=[dict(value) for value in (turn_schemas or [])],
                expected_authorization=(
                    dict(turn_authorization)
                    if isinstance(turn_authorization, Mapping)
                    else None
                ),
                output_authorizer=output_authorization_callback,
                input_authorizer=input_authorization_callback,
                document_thread_dispatch=document_thread_dispatch,
                debug_sink=(debug_events.append if debug_events is not None else None),
            ),
        )
        return NativeProviderToolRunner(
            execution=execution,
            document_dispatch=lambda operation: _on_document_thread(
                document_thread_dispatch,
                operation,
            ),
            refresh_context=lambda: _build_context_for_provider(
                service,
                session_trigger,
                document_thread_dispatch,
            ),
            frozen_surface=dict(turn_surface),
            frozen_schemas=[dict(value) for value in (turn_schemas or [])],
            frozen_modeling_surface=dict(turn_modeling_surface or {}),
            tool_trace=tool_trace,
            debug_events=debug_events,
            debug_capture_directory=(
                str(debug_config.get("capture_directory") or "")
                if debug_events is not None
                else ""
            ),
            progress_callback=progress_callback,
            cancellation_check=cancellation_check,
            steering_check=steering_check,
        )
    operation_manager = _vibescript_operation_manager(service)
    operation_local = threading.local()
    caller_progress_callback = progress_callback

    def routed_progress(event: dict[str, Any]) -> None:
        operation_id = str(
            getattr(operation_local, "operation_id", "") or ""
        )
        if operation_id:
            operation_manager.record_progress(operation_id, event)
        if caller_progress_callback is not None:
            caller_progress_callback(event)

    progress_callback = routed_progress
    frozen_schemas = json.loads(json.dumps(turn_schemas or []))
    frozen_modeling_surface = json.loads(json.dumps(turn_modeling_surface or {}))
    component_catalog_state: dict[str, Any] = {
        "prepared": (
            dict(turn_component_catalog)
            if isinstance(turn_component_catalog, Mapping)
            else None
        ),
        "dirty": False,
    }
    editable_sources_state: dict[str, Any] = {
        "prepared": (
            dict(turn_editable_sources)
            if isinstance(turn_editable_sources, Mapping)
            else None
        ),
    }
    source_lifecycle_tools = frozenset(
        {
            "vibescript.create_assembly",
            "vibescript.create_part",
            "vibescript.create_program",
            "vibescript.build_program",
            "vibescript.edit_source",
            "vibescript.set_inputs",
            "vibescript.reconfigure_program",
            "vibescript.delete_output",
            "vibescript.delete_program",
        }
    )

    def refresh_editable_sources(active_workbench: str | None) -> str:
        """Refresh mutable source authority without changing the frozen tool surface."""

        pack = vibescript_domains.get_vibescript_pack(active_workbench)
        if pack is None:
            return "The active workbench has no editable VibeScript source domain."
        try:
            captured = _on_document_thread(
                document_thread_dispatch,
                lambda: _capture_editable_sources_for_workbench(
                    service,
                    active_workbench,
                ),
            )
            editable_sources_state["prepared"] = _complete_editable_sources_for_workbench(
                captured
            )
        except Exception as exc:
            return str(exc)
        return ""

    def apply_source_lifecycle_result(
        tool_name: str,
        payload: Mapping[str, Any],
        active_workbench: str | None,
    ) -> None:
        """Keep a source returned by this turn authorized even if indexing lags."""

        active_pack = vibescript_domains.get_vibescript_pack(active_workbench)
        if active_pack is None:
            return
        source_target = payload.get("source_target")
        target = dict(source_target) if isinstance(source_target, Mapping) else {}
        failed_candidate = payload.get("failed_candidate")
        failed = dict(failed_candidate) if isinstance(failed_candidate, Mapping) else {}
        source_id = str(
            payload.get("source_id")
            or payload.get("program_id")
            or failed.get("program_id")
            or ""
        ).strip().lower()
        if len(source_id) != 32 or any(
            character not in "0123456789abcdef" for character in source_id
        ):
            return
        domain = str(
            target.get("domain") or payload.get("domain") or active_pack.domain
        ).strip().lower()
        current = (
            dict(editable_sources_state["prepared"])
            if isinstance(editable_sources_state.get("prepared"), Mapping)
            else {}
        )
        authoring_domains = {
            str(value or "").strip().lower()
            for value in list(current.get("authoring_domains") or [])
        }
        if domain != active_pack.domain and domain not in authoring_domains:
            return
        target_pack = vibescript_domains.get_vibescript_pack_for_domain(domain)
        if target_pack is None:
            return
        program_reference = str(
            target.get("program") or payload.get("program") or ""
        )
        _document_name, _program_domain, program_name = _program_reference_key(
            program_reference
        )
        if not program_name:
            return
        sources = [
            dict(item)
            for item in list(current.get("sources") or [])
            if isinstance(item, Mapping)
            and str(item.get("source_id") or "").strip().lower() != source_id
        ]
        all_sources = [
            dict(item)
            for item in list(current.get("all_sources") or sources)
            if isinstance(item, Mapping)
            and str(item.get("source_id") or "").strip().lower() != source_id
        ]
        if tool_name == "vibescript.delete_program" and payload.get("ok") is True:
            current["sources"] = sources
            current["source_count"] = len(sources)
            if "all_sources" in current:
                current["all_sources"] = all_sources
                current["all_source_count"] = len(all_sources)
            editable_sources_state["prepared"] = current
            return

        revision = str(
            payload.get("working_revision")
            or payload.get("next_write_expected_revision")
            or failed.get("revision")
            or target.get("current_revision")
            or ""
        ).strip().lower()
        if len(revision) != 64 or any(
            character not in "0123456789abcdef" for character in revision
        ):
            return
        raw_outputs = payload.get("live_outputs")
        if isinstance(raw_outputs, Mapping):
            affected_outputs = [
                {
                    "name": str(name),
                    **(
                        {
                            "object_name": str(details.get("object_name") or ""),
                            "label": str(details.get("label") or ""),
                        }
                        if isinstance(details, Mapping)
                        else {}
                    ),
                }
                for name, details in raw_outputs.items()
                if str(name)
            ]
        else:
            affected_outputs = [
                dict(item)
                for item in list(target.get("affected_outputs") or [])
                if isinstance(item, Mapping)
            ]
        record = {
            "source_id": source_id,
            "source_kind": "vibescript_program",
            "domain": domain,
            "workbench": str(target_pack.workbench),
            "label": program_name,
            "program": program_reference,
            "current_revision": revision,
            "status": "accepted" if payload.get("ok") is True else "build_failed",
            "affected_outputs": affected_outputs,
            "read_tool": "vibescript.read_source",
            "build_tool": "vibescript.build_program",
            "edit_tool": "vibescript.edit_source",
            "delete_output_tool": "vibescript.delete_output",
            "delete_program_tool": "vibescript.delete_program",
            "build_arguments": {
                "program": program_reference,
                "expected_revision": revision,
            },
            "edit_target_arguments": {
                "program": program_reference,
                "expected_revision": revision,
            },
            "delete_target_arguments": {
                "program": program_reference,
                "expected_revision": revision,
                "reason": "Remove this source and its owned outputs.",
            },
        }
        if domain == str(current.get("domain") or active_pack.domain):
            sources.append(record)
        all_sources.append(record)
        current.update(
            {
                "schema": "vibecad-editable-sources-v1",
                "domain": str(current.get("domain") or active_pack.domain),
                "workbench": str(
                    current.get("workbench") or active_pack.workbench
                ),
                "source_count": len(sources),
                "sources": sources,
            }
        )
        if "all_sources" in current or authoring_domains:
            current["all_sources"] = all_sources
            current["all_source_count"] = len(all_sources)
        editable_sources_state["prepared"] = current

    def run(
        tool_name: str,
        arguments_json: str = "{}",
        provider_call_id: str = "",
    ) -> dict[str, Any]:
        del provider_call_id  # VibeScript does not consume Native call ownership.
        started = time.monotonic()
        tool = None
        args: dict[str, Any] = {}

        def finalize(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal args, tool
            source_lifecycle_result = bool(
                payload.get("_vibecad_source_lifecycle_result")
            )
            operation_started = (
                tool_name in VIBESCRIPT_BACKGROUND_SOURCE_TOOLS
                and isinstance(payload.get("operation"), Mapping)
                and payload["operation"].get("status") == "running"
            )
            if not bool(payload.get("ok")):
                payload = normalize_tool_failure(tool_name, args, payload)
                if source_lifecycle_result:
                    # Failure normalization deliberately rebuilds the public
                    # failure contract. Retain this internal projection marker
                    # so terminal background reads still receive the concise
                    # source lifecycle envelope and its exact recovery calls.
                    payload["_vibecad_source_lifecycle_result"] = True
            else:
                if not operation_started and tool is not None and tool.safety in {
                    SafetyLevel.SAFE_WRITE,
                    SafetyLevel.WRITE,
                }:
                    component_catalog_state["dirty"] = True
                if not operation_started and tool_name not in {
                    "vibescript.read_source",
                    "vibescript.read_api",
                    VIBESCRIPT_READ_OPERATION_TOOL,
                }:
                    _on_document_thread(
                        document_thread_dispatch,
                        lambda: service.note_provider_tool_targets(args, payload),
                    )
            trace_payload = dict(payload)
            trace_payload.pop("_vibecad_image_attachment", None)
            trace_payload.pop("_vibecad_complete_source_result", None)
            trace_payload.pop("_vibecad_complete_api_result", None)
            trace_payload.pop("_vibecad_source_lifecycle_result", None)
            trace_payload.pop("_vibecad_source_read_result", None)
            trace_payload.pop("_vibecad_geometry_read_request", None)
            trace_result = _trace_result(trace_payload)
            trace = {
                "tool_name": tool_name,
                "arguments": args,
                "safety": tool.safety.value if tool is not None else None,
                "workbench": tool.workbench if tool is not None else None,
                "ok": bool(payload.get("ok")),
                "elapsed_seconds": round(time.monotonic() - started, 4),
                "result": trace_result,
            }
            tool_trace.append(trace)
            _emit(
                progress_callback,
                {
                    "event": "tool_call_completed",
                    "tool_name": tool_name,
                    "ok": bool(payload.get("ok")),
                    "result": trace_result,
                },
            )
            return payload

        if cancellation_check is not None and cancellation_check():
            return finalize(
                tool_failure(
                    tool_name,
                    "RUN_CANCELLED",
                    "precondition",
                    "VibeCAD run stopped before this tool executed.",
                    requested={"arguments_json": arguments_json},
                    observed={"cancel_requested": True},
                    cancelled=True,
                )
            )
        executing_background = bool(
            getattr(operation_local, "executing", False)
        )
        if not executing_background:
            preflight_args, preflight_error = _parse_arguments(arguments_json)
            if tool_name == VIBESCRIPT_READ_OPERATION_TOOL:
                if preflight_error:
                    return finalize(
                        tool_failure(
                            tool_name,
                            "INVALID_TOOL_ARGUMENTS_JSON",
                            "schema",
                            preflight_error,
                            requested={"arguments_json": arguments_json},
                            observed={"expected": "JSON object"},
                        )
                    )
                args = dict(preflight_args or {})
                try:
                    tool = service.registry.get(tool_name)
                except KeyError:
                    return finalize(
                        tool_failure(
                            tool_name,
                            "UNKNOWN_TOOL",
                            "surface",
                            f"Unknown VibeCAD tool: {tool_name}",
                            requested=args,
                        )
                    )
                try:
                    tool.spec.validate_arguments(args)
                except ToolArgumentValidationError as exc:
                    return finalize(exc.payload)
                return finalize(
                    operation_manager.read(
                        str(args["operation_id"]),
                        float(args.get("wait_seconds", 30) or 0),
                    )
                )
            active_operation = operation_manager.active()
            if active_operation is not None:
                args = dict(preflight_args or {})
                payload = tool_failure(
                    tool_name,
                    "VIBESCRIPT_OPERATION_ACTIVE",
                    "precondition",
                    (
                        "A VibeScript mutation is still running. Read its "
                        "operation status before calling another CAD tool."
                    ),
                    requested=args,
                    observed={"active_operation": active_operation},
                    required_changes=[
                        {
                            "tool": VIBESCRIPT_READ_OPERATION_TOOL,
                            "arguments": {
                                "operation_id": active_operation["operation_id"],
                            },
                        }
                    ],
                )
                payload["active_operation"] = active_operation
                payload["next_action"] = {
                    "tool": VIBESCRIPT_READ_OPERATION_TOOL,
                    "arguments": {
                        "operation_id": active_operation["operation_id"],
                    },
                }
                return finalize(payload)
        live_surface = _on_document_thread(
            document_thread_dispatch,
            lambda: _live_provider_surface_state(service),
        )
        active_workbench = live_surface["workbench"]
        runtime_state = live_surface["runtime_state"]
        visible_names = live_surface["tool_names"]
        if isinstance(turn_surface, dict):
            expected_tuple = _surface_authorization_tuple(
                str(turn_surface.get("workbench") or ""),
                str(turn_surface.get("engine") or ""),
                str(turn_surface.get("surface_id") or ""),
            )
            observed_tuple = _surface_authorization_tuple(
                active_workbench,
                str(live_surface.get("engine") or ""),
                str(live_surface.get("surface_id") or ""),
            )
            if observed_tuple != expected_tuple:
                return finalize(
                    tool_failure(
                        tool_name,
                        "TURN_SURFACE_INVALIDATED",
                        "surface",
                        "The active workbench changed after this turn started. "
                        "Start the next turn with its current API.",
                        requested={"arguments_json": arguments_json},
                        observed={
                            "turn_start": {
                                "authoring_surface": expected_tuple[0],
                                "engine": expected_tuple[1],
                                "surface_id": expected_tuple[2],
                            },
                            "live": {
                                "authoring_surface": observed_tuple[0],
                                "engine": observed_tuple[1],
                                "surface_id": observed_tuple[2],
                            },
                            "unavailable_reason": live_surface.get(
                                "unavailable_reason"
                            ),
                        },
                        candidates=visible_names,
                        required_changes=[{"start_next_turn": True}],
                    )
                )
        try:
            tool = service.registry.get(tool_name)
        except KeyError:
            return finalize(
                tool_failure(
                    tool_name,
                    "UNKNOWN_TOOL",
                    "surface",
                    f"Unknown VibeCAD tool: {tool_name}",
                    requested={"arguments_json": arguments_json},
                    observed={
                        "active_workbench": active_workbench,
                        "active_edit_mode": runtime_state.get("edit_mode"),
                    },
                    candidates=visible_names,
                    required_changes=[{"choose_available_tool": visible_names}],
                )
            )
        if tool_name not in visible_names:
            return finalize(
                tool_failure(
                    tool_name,
                    "TOOL_NOT_ON_ACTIVE_SURFACE",
                    "surface",
                    f"Tool is not in the active provider surface: {tool_name}.",
                    requested={"arguments_json": arguments_json},
                    observed={
                        "active_workbench": active_workbench,
                        "active_edit_mode": runtime_state.get("edit_mode"),
                        "active_edit_object": _active_sketch_name(runtime_state)
                        or None,
                    },
                    candidates=visible_names,
                    required_changes=[{"choose_available_tool": visible_names}],
                )
            )
        args, argument_error = _parse_arguments(arguments_json)
        if argument_error:
            args = {}
            return finalize(
                tool_failure(
                    tool_name,
                    "INVALID_TOOL_ARGUMENTS_JSON",
                    "schema",
                    argument_error,
                    requested={"arguments_json": arguments_json},
                    observed={"expected": "JSON object"},
                    required_changes=[{"provide": "one valid JSON object"}],
                )
            )
        assert args is not None
        try:
            tool.spec.validate_arguments(args)
        except ToolArgumentValidationError as exc:
            return finalize(exc.payload)
        if not executing_background:
            if tool_name in VIBESCRIPT_BACKGROUND_SOURCE_TOOLS:

                def execute_background(operation_id: str) -> dict[str, Any]:
                    operation_local.executing = True
                    operation_local.operation_id = operation_id
                    try:
                        return run(tool_name, arguments_json)
                    finally:
                        operation_local.operation_id = ""
                        operation_local.executing = False

                return finalize(
                    operation_manager.start(
                        tool_name,
                        args,
                        execute_background,
                    )
                )
        if tool_name == "conversation.ask_user":
            questions = args.get("questions")
            assert isinstance(questions, list) and questions
            if question_callback is None:
                return finalize(
                    tool_failure(
                        tool_name,
                        "QUESTION_UI_UNAVAILABLE",
                        "precondition",
                        "The interactive question UI is unavailable in this session.",
                        requested=args,
                        observed={"question_count": len(questions)},
                    )
                )
            try:
                answers = question_callback(questions)
            except Exception as exc:
                completed_answers = list(getattr(exc, "completed_answers", []) or [])
                return finalize(
                    tool_failure(
                        tool_name,
                        "QUESTION_ROUND_FAILED",
                        "precondition",
                        f"The question round failed: {exc}",
                        requested=args,
                        observed={
                            "question_count": len(questions),
                            "completed_answer_count": len(completed_answers),
                        },
                        completed_answers=completed_answers,
                    )
                )
            payload = {
                "ok": bool(answers),
                "answers": answers,
                "cancelled": not bool(answers),
            }
            if not answers:
                payload = tool_failure(
                    tool_name,
                    "QUESTION_ROUND_CANCELLED",
                    "precondition",
                    "The user cancelled the question round.",
                    requested=args,
                    observed={"question_count": len(questions)},
                    cancelled=True,
                    answers=[],
                )
            return finalize(payload)
        if tool_name == "conversation.review_design":
            if not provider_calls_allowed:
                return finalize(
                    tool_failure(
                        tool_name,
                        "PROVIDER_CALL_DISABLED",
                        "precondition",
                        (
                            "This controller supplies CAD tools only and cannot "
                            "start a VibeCAD model or provider."
                        ),
                        requested=args,
                    )
                )
            from VibeCADDesignReview import run_design_review

            review_context = _build_context_for_provider(
                service,
                session_trigger,
                document_thread_dispatch,
            )
            _emit(
                progress_callback,
                {"event": "design_review_started"},
            )
            try:
                review = run_design_review(
                    provider=service.provider_name(),
                    model=service.provider_model(),
                    api_key=service.provider_api_key(),
                    base_url=service.provider_base_url(),
                    reasoning_effort=service.provider_reasoning_effort(),
                    customer_intent=str(args["customer_intent"]),
                    design_draft=str(args["design_draft"]),
                    context=review_context,
                    cancellation_check=cancellation_check,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                _emit(
                    progress_callback,
                    {"event": "design_review_failed", "error": str(exc)},
                )
                return finalize(
                    tool_failure(
                        tool_name,
                        "DESIGN_REVIEW_FAILED",
                        "external_process",
                        f"Independent design review failed: {exc}",
                        requested=args,
                        observed={"provider": service.provider_name()},
                    )
                )
            _emit(
                progress_callback,
                {
                    "event": "design_review_completed",
                    "verdict": review.get("verdict"),
                    "finding_count": len(review.get("findings") or []),
                },
            )
            return finalize({"ok": True, "review": review})
        if tool_name == "vibescript.delete_object":
            from VibeCADObjectDeletion import (
                ObjectDeletionError,
                delete_exact_object,
            )

            try:
                payload = _on_document_thread(
                    document_thread_dispatch,
                    lambda: delete_exact_object(service, args),
                )
            except ObjectDeletionError as exc:
                payload = tool_failure(
                    tool_name,
                    exc.code,
                    "precondition",
                    str(exc),
                    requested=args,
                    observed=exc.observed,
                )
            return finalize(payload)
        if tool_name == "component_catalog.search":
            from tool_impl.service.component_catalog_search import (
                capture,
                complete,
                prepare,
            )

            idle_state = _wait_for_document_idle(
                service,
                document_thread_dispatch,
                cancellation_check,
                progress_callback,
            )
            if not idle_state.get("ok"):
                return finalize(_document_idle_failure(tool_name, args, idle_state))
            try:
                prepared = component_catalog_state.get("prepared")
                if component_catalog_state.get("dirty") or not isinstance(
                    prepared, Mapping
                ):
                    captured = _on_document_thread(
                        document_thread_dispatch,
                        lambda: capture(service),
                    )
                    prepared = prepare(captured)
                    component_catalog_state["prepared"] = prepared
                    component_catalog_state["dirty"] = False
                payload = complete(dict(prepared), **args)
                return finalize({"ok": True, **payload})
            except Exception as exc:
                return finalize(
                    tool_failure(
                        tool_name,
                        "COMPONENT_CATALOG_SEARCH_FAILED",
                        "precondition",
                        str(exc),
                        requested=args,
                        observed={"exception_type": exc.__class__.__name__},
                    )
                )
        if tool.spec.requires_document:
            idle_state = _wait_for_document_idle(
                service,
                document_thread_dispatch,
                cancellation_check,
                progress_callback,
            )
            if not idle_state.get("ok"):
                return finalize(_document_idle_failure(tool_name, args, idle_state))
        state_before = _on_document_thread(
            document_thread_dispatch,
            lambda: _minimal_runtime_state(service),
        )
        edit_block = _edit_mode_block(tool, state_before)
        if edit_block is not None:
            edit_block["requested"] = args
            return finalize(edit_block)
        if tool_name in {
            "vibescript.create_assembly",
            "vibescript.create_part",
            "vibescript.read_source",
            "vibescript.read_api",
            "vibescript.read_geometry",
            "vibescript.read_placement",
            "vibescript.create_program",
            "vibescript.build_program",
            "vibescript.edit_source",
            "vibescript.set_inputs",
            "vibescript.reconfigure_program",
            "vibescript.delete_output",
            "vibescript.delete_program",
        }:
            source_workbench = active_workbench
            if isinstance(turn_surface, dict) and share_authoring_surface(
                str(turn_surface.get("workbench") or ""),
                active_workbench,
            ):
                # Keep compatibility calls without an explicit domain bound to
                # the turn-start default.  Changing ribbons is presentation,
                # not permission to redirect a source write mid-turn.
                source_workbench = str(turn_surface.get("workbench") or "")
            payload = _run_universal_vibescript_tool(
                service,
                source_workbench,
                tool_name,
                args,
                component_catalog=(
                    component_catalog_state.get("prepared")
                    if isinstance(
                        component_catalog_state.get("prepared"), Mapping
                    )
                    else None
                ),
                editable_sources=(
                    editable_sources_state.get("prepared")
                    if isinstance(editable_sources_state.get("prepared"), Mapping)
                    else None
                ),
                document_thread_dispatch=document_thread_dispatch,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            )
            if tool_name in source_lifecycle_tools:
                refresh_error = refresh_editable_sources(source_workbench)
                apply_source_lifecycle_result(tool_name, payload, source_workbench)
                if refresh_error:
                    payload.setdefault("warnings", []).append(
                        {
                            "code": "EDITABLE_SOURCE_INDEX_REFRESH_FAILED",
                            "error": refresh_error,
                        }
                    )
            return finalize(payload)
        if (
            vibescript_domains.get_domain_adapter(
                tool_name.split(".")[1]
                if tool_name.startswith("vibescript.") and tool_name.count(".") == 2
                else ""
            )
            is not None
        ):
            return finalize(
                _run_domain_vibescript_tool(
                    service,
                    tool_name,
                    args,
                    document_thread_dispatch=document_thread_dispatch,
                    cancellation_check=cancellation_check,
                    progress_callback=progress_callback,
                )
            )
        if tool_name == "drawing.sources":
            _prepare_responsive_drawing_source_catalog(
                service,
                document_thread_dispatch,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            )
        _emit(
            progress_callback,
            {
                "event": "native_tool_document_phase_started",
                "tool_name": tool_name,
            },
        )
        document_phase_started = time.monotonic()
        try:
            raw = _on_document_thread(
                document_thread_dispatch,
                lambda: service.registry.call(tool_name, **args),
            )
            payload = dict(raw) if isinstance(raw, dict) else {"value": raw}
            payload.setdefault("ok", payload.get("error") in (None, ""))
        except ToolArgumentValidationError as exc:
            payload = exc.payload
        except Exception as exc:
            payload = tool_failure(
                tool_name,
                "TOOL_HANDLER_EXCEPTION",
                "native_call",
                str(exc),
                requested=args,
                observed={"exception_type": exc.__class__.__name__},
            )
        document_thread_elapsed = round(
            time.monotonic() - document_phase_started,
            4,
        )
        payload.setdefault(
            "document_thread_elapsed_seconds",
            document_thread_elapsed,
        )
        _emit(
            progress_callback,
            {
                "event": "native_tool_document_phase_completed",
                "tool_name": tool_name,
                "elapsed_seconds": document_thread_elapsed,
                "ok": bool(payload.get("ok")),
            },
        )
        try:
            steering = _consume_steering(steering_check)
        except Exception as exc:
            steering = []
            payload["human_steering_error"] = str(exc)
        if steering:
            payload["human_steering"] = steering
            _emit(
                progress_callback,
                {"event": "human_steering_consumed", "message_count": len(steering)},
            )
        return finalize(payload)

    def provider_update() -> dict[str, Any]:
        refreshed = _build_context_for_provider(
            service,
            session_trigger,
            document_thread_dispatch,
            (
                None
                if component_catalog_state.get("dirty")
                else component_catalog_state.get("prepared")
            ),
        )
        refreshed_catalog = refreshed.get("_vibecad_component_catalog")
        component_catalog_state["prepared"] = (
            dict(refreshed_catalog) if isinstance(refreshed_catalog, Mapping) else None
        )
        component_catalog_state["dirty"] = False
        completed = refreshed
        _consume_context_view_attachment(service, completed, document_thread_dispatch)
        if not isinstance(turn_surface, dict):
            refreshed_sources = completed.get("editable_sources")
            if isinstance(refreshed_sources, Mapping):
                editable_sources_state["prepared"] = dict(refreshed_sources)
            return completed

        live_surface = dict(completed.get("provider_tool_surface") or {})
        expected_tuple = _surface_authorization_tuple(
            str(turn_surface.get("workbench") or ""),
            str(turn_surface.get("engine") or ""),
            str(turn_surface.get("surface_id") or ""),
        )
        live_tuple = _surface_authorization_tuple(
            str(live_surface.get("workbench") or ""),
            str(live_surface.get("engine") or ""),
            str(live_surface.get("surface_id") or ""),
        )
        completed["provider_tool_surface"] = dict(turn_surface)
        completed["provider_tool_schemas"] = json.loads(json.dumps(frozen_schemas))
        if isinstance(turn_authorization, Mapping):
            completed["_native_turn_authorization"] = json.loads(
                json.dumps(dict(turn_authorization))
            )
        completed["workbench"] = str(turn_surface.get("workbench") or "") or None
        if frozen_modeling_surface:
            completed["modeling_surface"] = json.loads(
                json.dumps(frozen_modeling_surface)
            )
        if live_tuple != expected_tuple:
            # Never inject the next workbench/domain into an in-flight turn.
            # Calls remain authorized against the frozen tuple and will return
            # TURN_SURFACE_INVALIDATED until the human starts the next turn.
            for key in (
                "partdesign",
                "vibescript",
                "vibescript_domain",
                "sketcher",
                "part",
                "assembly",
                "surface",
                "draft",
                "techdraw",
                "cam",
                "fem",
                "material",
                "mesh",
                "meshpart",
                "points",
                "spreadsheet",
                "inspection",
                "robot",
                "reverse_engineering",
                "editable_sources",
                "available_components",
                "_vibecad_component_catalog",
            ):
                completed.pop(key, None)
            completed["modeling_surface"] = {
                **dict(completed.get("modeling_surface") or {}),
                "invalidated": True,
                "live_tuple": {
                    "workbench": live_tuple[0],
                    "engine": live_tuple[1],
                    "surface_id": live_tuple[2],
                },
                "next_turn_required": True,
            }
        else:
            refreshed_sources = completed.get("editable_sources")
            if isinstance(refreshed_sources, Mapping):
                rebased_sources = _rebase_unified_source_index(
                    refreshed_sources,
                    str(turn_surface.get("workbench") or ""),
                )
                completed["editable_sources"] = rebased_sources
                editable_sources_state["prepared"] = rebased_sources
        return completed

    run.provider_update = provider_update
    return run


def _run_session_turn(
    prompt: str,
    *,
    service: VibeCADService | None,
    prefer_online: bool,
    provider: BaseProvider | None,
    progress_callback: ProgressCallback | None,
    cancellation_check: CancellationCheck | None,
    steering_check: SteeringCheck | None,
    question_callback: QuestionCallback | None,
    output_authorization_callback: NativeOutputAuthorizer | None,
    input_authorization_callback: NativeInputAuthorizer | None,
    session_trigger: dict[str, Any] | None,
    persist_input_as_user: bool,
    prompt_section: str,
    document_thread_dispatch: DocumentThreadDispatch | None,
) -> VibeCADResponse:
    from VibeCADMCP import require_internal_agent

    require_internal_agent()
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Prompt cannot be empty.")
    active_service = service or _on_document_thread(
        document_thread_dispatch,
        get_service,
    )
    document_state = _on_document_thread(
        document_thread_dispatch,
        active_service.assistant_document_state,
    )
    if not document_state.get("enabled"):
        raise RuntimeError(
            str(
                document_state.get("message")
                or "Create or open a document to use VibeCAD."
            )
        )
    if not document_state.get("turn_enabled", True):
        raise RuntimeError(
            str(
                document_state.get("message")
                or "Choose Native or VibeScript to begin."
            )
        )
    turn_conversation_id: str | None = None
    turn_conversation: list[dict[str, Any]] = []
    if persist_input_as_user:
        recorded = _persist_session_conversation_turn(
            active_service,
            "user",
            clean_prompt,
            dispatch=document_thread_dispatch,
        )
        turn_conversation_id = str(recorded.get("conversation_id") or "") or None
        turn_conversation = [
            dict(item)
            for item in recorded.get("conversation") or []
            if isinstance(item, dict)
        ]
    else:
        recorded = _load_conversation_for_session(
            active_service,
            document_thread_dispatch,
        )
        turn_conversation_id = str(recorded.get("conversation_id") or "") or None
        turn_conversation = [
            dict(item)
            for item in recorded.get("conversation") or []
            if isinstance(item, dict)
        ]
    _emit(progress_callback, {"event": "context_build_started"})
    context = _build_context_for_provider(
        active_service,
        session_trigger,
        document_thread_dispatch,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )
    if turn_conversation_id:
        context["_vibecad_codex_session"] = {
            "conversation_id": turn_conversation_id,
            "conversation_path": str(recorded.get("path") or ""),
        }
    _consume_context_view_attachment(active_service, context, document_thread_dispatch)
    tool_trace: list[dict[str, Any]] = []
    provider_prompt = _provider_prompt(
        clean_prompt,
        context,
        prompt_section=prompt_section,
        recent_conversation=turn_conversation,
        current_user_message=clean_prompt if persist_input_as_user else None,
    )
    input_budget = provider_input_budget(provider_prompt, context)
    _emit(
        progress_callback,
        {
            "event": "context_build_completed",
            "workbench": context.get("workbench"),
            "provider_tool_count": len(context.get("provider_tool_schemas") or []),
            "input_budget": input_budget,
        },
    )
    active_provider = provider or _on_document_thread(
        document_thread_dispatch,
        lambda: choose_provider(
            active_service,
            prefer_online=prefer_online,
        ),
    )
    provider_name = active_provider.__class__.__name__
    provider_runtime = provider_execution_identity(active_provider)
    tool_runner = make_provider_tool_runner(
        active_service,
        tool_trace=tool_trace,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
        question_callback=question_callback,
        output_authorization_callback=output_authorization_callback,
        input_authorization_callback=input_authorization_callback,
        session_trigger=session_trigger,
        document_thread_dispatch=document_thread_dispatch,
        turn_surface=(
            dict(context["provider_tool_surface"])
            if isinstance(context.get("provider_tool_surface"), dict)
            and context["provider_tool_surface"].get("kind") == "turn_start_snapshot"
            else None
        ),
        turn_schemas=[
            dict(schema)
            for schema in list(context.get("provider_tool_schemas") or [])
            if isinstance(schema, dict)
        ],
        turn_authorization=(
            dict(context["_native_turn_authorization"])
            if isinstance(context.get("_native_turn_authorization"), Mapping)
            else None
        ),
        turn_modeling_surface=(
            dict(context["modeling_surface"])
            if isinstance(context.get("modeling_surface"), dict)
            else None
        ),
        turn_component_catalog=(
            dict(context["_vibecad_component_catalog"])
            if isinstance(context.get("_vibecad_component_catalog"), Mapping)
            else None
        ),
        turn_editable_sources=(
            dict(context["editable_sources"])
            if isinstance(context.get("editable_sources"), Mapping)
            else None
        ),
    )
    _emit(
        progress_callback,
        {
            "event": "provider_turn_started",
            "provider": provider_name,
            "provider_runtime": provider_runtime,
            "turn": 1,
        },
    )
    try:
        result = _run_provider(
            active_provider,
            provider_prompt,
            context,
            tool_runner,
            cancellation_check,
            progress_callback,
        )
        final_output = str(result.final_output or "").strip()
        if final_output:
            turn_metadata: dict[str, Any] = {
                "provider_runtime": provider_runtime,
            }
            if session_trigger:
                turn_metadata["session_trigger"] = session_trigger
            _persist_session_conversation_turn(
                active_service,
                "assistant",
                final_output,
                provider=provider_name,
                metadata=turn_metadata,
                conversation_id=turn_conversation_id,
                dispatch=document_thread_dispatch,
            )
            _emit(
                progress_callback,
                {
                    "event": "provider_turn_output",
                    "provider": provider_name,
                    "provider_runtime": provider_runtime,
                    "turn": 1,
                    "text": final_output,
                },
            )
        final_context = _build_context_for_provider(
            active_service,
            session_trigger,
            document_thread_dispatch,
        )
        _emit(
            progress_callback,
            {
                "event": "provider_turn_completed",
                "provider": provider_name,
                "provider_runtime": provider_runtime,
                "turn": 1,
                "tool_count": len(tool_trace),
            },
        )
        return VibeCADResponse(
            provider=provider_name,
            final_output=final_output,
            context=final_context,
            tool_trace=tool_trace,
        )
    except ProviderUnavailable as exc:
        provider_error = str(exc)
        final_output = f"{provider_name} failed before returning a usable AI result: {provider_error}"
        _emit(
            progress_callback,
            {
                "event": "provider_turn_failed",
                "provider": provider_name,
                "provider_runtime": provider_runtime,
                "turn": 1,
                "error": str(exc),
                "tool_count": len(tool_trace),
            },
        )
        failed_context = _build_context_for_provider(
            active_service,
            session_trigger,
            document_thread_dispatch,
        )
        return VibeCADResponse(
            provider=provider_name,
            final_output=final_output,
            context=failed_context,
            tool_trace=tool_trace,
            error=str(exc),
        )
    finally:
        close_tool_runner = getattr(tool_runner, "close", None)
        if callable(close_tool_runner):
            close_tool_runner()


def run_prompt(
    prompt: str,
    service: VibeCADService | None = None,
    prefer_online: bool = True,
    provider: BaseProvider | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    steering_check: SteeringCheck | None = None,
    question_callback: QuestionCallback | None = None,
    output_authorization_callback: NativeOutputAuthorizer | None = None,
    input_authorization_callback: NativeInputAuthorizer | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
) -> VibeCADResponse:
    return _run_session_turn(
        prompt,
        service=service,
        prefer_online=prefer_online,
        provider=provider,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
        question_callback=question_callback,
        output_authorization_callback=output_authorization_callback,
        input_authorization_callback=input_authorization_callback,
        session_trigger=None,
        persist_input_as_user=True,
        prompt_section="CURRENT_USER_MESSAGE",
        document_thread_dispatch=document_thread_dispatch,
    )


def rebuild_intent_memory(
    service: VibeCADService | None = None,
    prefer_online: bool = True,
    provider: BaseProvider | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
) -> dict[str, Any]:
    """Recompile durable intent from all persisted project conversations."""
    from VibeCADMCP import require_internal_agent

    require_internal_agent()
    active_service = service or _on_document_thread(
        document_thread_dispatch, get_service
    )
    persistence = _on_document_thread(
        document_thread_dispatch, active_service.document_persistence_state
    )
    if not persistence.get("enabled"):
        raise RuntimeError(
            str(persistence.get("message") or "Save the document before rebuilding.")
        )
    if not active_service.intent_memory_enabled():
        raise RuntimeError("Enable Intent Memory in VibeCAD preferences first.")
    snapshot = _on_document_thread(
        document_thread_dispatch, active_service.intent_memory_rebuild_snapshot
    )
    pending = list(snapshot.get("uncovered_turns") or [])
    if not pending:
        return {
            "ok": True,
            "changed": False,
            "reason": "no_conversation_turns",
            "revision": snapshot["current_revision"],
        }
    active_provider = provider or _on_document_thread(
        document_thread_dispatch,
        lambda: choose_provider(active_service, prefer_online=prefer_online),
    )
    if isinstance(active_provider, AnthropicProvider):
        provider_id = "anthropic"
    elif isinstance(active_provider, GeminiProvider):
        provider_id = "gemini"
    elif isinstance(active_provider, CodexProvider):
        provider_id = active_provider.provider_id
    else:
        raise ProviderUnavailable("Intent Memory rebuild requires an online provider.")
    _emit(
        progress_callback,
        {"event": "intent_memory_update_started", "turn_count": len(pending)},
    )
    update = compile_intent_memory_update(
        provider=provider_id,
        model=active_service.intent_memory_model(),
        api_key=active_service.provider_api_key(),
        base_url=active_service.provider_base_url(),
        memory=snapshot["memory"],
        uncovered_turns=pending,
        debug_context={"_vibecad_debug": active_service.provider_debug_config()},
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )
    committed = _on_document_thread(
        document_thread_dispatch,
        lambda: active_service.apply_intent_memory_rebuild(
            update,
            expected_current_revision=snapshot["current_revision"],
        ),
    )
    _emit(
        progress_callback,
        {
            "event": "intent_memory_update_completed",
            "revision": committed.get("revision"),
            "entry_count": len(committed.get("entries") or []),
        },
    )
    return {
        "ok": True,
        "changed": True,
        "revision": committed.get("revision"),
        "entry_count": len(committed.get("entries") or []),
    }


def run_sketch_close_continuation(
    event: dict[str, Any],
    service: VibeCADService | None = None,
    prefer_online: bool = True,
    provider: BaseProvider | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    steering_check: SteeringCheck | None = None,
    question_callback: QuestionCallback | None = None,
    output_authorization_callback: NativeOutputAuthorizer | None = None,
    input_authorization_callback: NativeInputAuthorizer | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
) -> VibeCADResponse:
    if not isinstance(event, dict):
        raise ValueError("Sketch-close continuation event must be an object.")
    expected_fields = {
        "type",
        "document_uid",
        "document_name",
        "sketch_name",
        "sketch_label",
        "owner_body",
    }
    if set(event) != expected_fields:
        raise ValueError(
            "Sketch-close continuation event requires exactly: "
            + ", ".join(sorted(expected_fields))
            + "."
        )
    if str(event.get("type") or "").strip() != "human_closed_sketch":
        raise ValueError(
            "Sketch-close continuation event type must be human_closed_sketch."
        )
    clean_event = {
        "type": "human_closed_sketch",
        "document_uid": str(event.get("document_uid") or "").strip(),
        "document_name": str(event.get("document_name") or "").strip(),
        "sketch_name": str(event.get("sketch_name") or "").strip(),
        "sketch_label": str(event.get("sketch_label") or "").strip(),
        "owner_body": str(event.get("owner_body") or "").strip(),
    }
    missing = [
        key
        for key in ("document_uid", "document_name", "sketch_name", "owner_body")
        if not clean_event[key]
    ]
    if missing:
        raise ValueError(
            "Sketch-close continuation event is missing: " + ", ".join(missing) + "."
        )
    prompt = (
        f"The human closed sketch {clean_event['sketch_name']} "
        f"({clean_event['sketch_label'] or clean_event['sketch_name']}) in Body "
        f"{clean_event['owner_body']}. Continue the existing CAD obligation from the "
        "current post-edit document state. Closing the sketch is a handoff to continue, "
        "not proof that the sketch is valid or permission to skip verification. Inspect "
        "its current readiness and native errors before choosing the next operation. Do "
        "not restart requirement refinement or restate the accepted design."
    )
    return _run_session_turn(
        prompt,
        service=service,
        prefer_online=prefer_online,
        provider=provider,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
        question_callback=question_callback,
        output_authorization_callback=output_authorization_callback,
        input_authorization_callback=input_authorization_callback,
        session_trigger=clean_event,
        persist_input_as_user=False,
        prompt_section="CURRENT_SESSION_EVENT",
        document_thread_dispatch=document_thread_dispatch,
    )


def run_native_surface_continuation(
    event: dict[str, Any],
    service: VibeCADService | None = None,
    prefer_online: bool = True,
    provider: BaseProvider | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    steering_check: SteeringCheck | None = None,
    question_callback: QuestionCallback | None = None,
    output_authorization_callback: NativeOutputAuthorizer | None = None,
    input_authorization_callback: NativeInputAuthorizer | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
) -> VibeCADResponse:
    if not isinstance(event, dict):
        raise ValueError("CAD continuation event must be an object.")
    event_type = str(event.get("type") or "").strip()
    expected_fields = {
        "type",
        "document_uid",
        "document_name",
        "surface_id",
        "workspace",
    }
    if event_type == "cad_edit_started":
        expected_fields.add("edit_object_name")
    if set(event) != expected_fields:
        raise ValueError(
            "CAD continuation event requires exactly: "
            + ", ".join(sorted(expected_fields))
            + "."
        )
    if event_type not in {
        "cad_workspace_changed",
        "cad_edit_started",
        "cad_provider_surface_changed",
    }:
        raise ValueError(
            "CAD continuation event type must be cad_workspace_changed, "
            "cad_edit_started, or cad_provider_surface_changed."
        )
    clean_event = {
        "type": event_type,
        "document_uid": str(event.get("document_uid") or "").strip(),
        "document_name": str(event.get("document_name") or "").strip(),
        "surface_id": str(event.get("surface_id") or "").strip(),
        "workspace": str(event.get("workspace") or "").strip(),
    }
    if event_type == "cad_edit_started":
        clean_event["edit_object_name"] = str(
            event.get("edit_object_name") or ""
        ).strip()
    missing = [key for key, value in clean_event.items() if not value]
    if missing:
        raise ValueError(
            "CAD continuation event is missing: "
            + ", ".join(sorted(missing))
            + "."
        )
    if event_type == "cad_edit_started" and clean_event["surface_id"] != "sketch.edit":
        raise ValueError("A CAD edit-start continuation requires sketch.edit.")
    workspace = clean_event["workspace"].replace("_", " ").capitalize()
    prompt = (
        f"{workspace} tools now match the current study state. Continue the existing "
        "engineering task without repeating completed work."
        if event_type == "cad_provider_surface_changed"
        else f"{workspace} work is now available. Continue the current design from its "
        "existing document state. Do not repeat completed operations."
    )
    return _run_session_turn(
        prompt,
        service=service,
        prefer_online=prefer_online,
        provider=provider,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
        question_callback=question_callback,
        output_authorization_callback=output_authorization_callback,
        input_authorization_callback=input_authorization_callback,
        session_trigger={"workspace": clean_event["workspace"]},
        persist_input_as_user=False,
        prompt_section="CURRENT_SESSION_EVENT",
        document_thread_dispatch=document_thread_dispatch,
    )


def _format_document_delta(delta: Any) -> str:
    if not isinstance(delta, dict):
        return ""
    added = delta.get("added") or []
    removed = delta.get("removed") or []
    changed = delta.get("changed") or []
    parts: list[str] = []
    if added:
        parts.append(f"+{len(added)} objects")
    if removed:
        parts.append(f"-{len(removed)} objects")
    if changed:
        parts.append(f"{len(changed)} changed")
    return ", ".join(parts)
