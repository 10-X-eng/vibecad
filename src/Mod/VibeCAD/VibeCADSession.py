# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD provider session orchestration.

The session owns context, tool exposure, execution, steering, cancellation,
and persistence. Product intent stays in the conversation. FreeCAD state stays
in the live state packet. There is no workflow phase machine or prose parser.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import time
from typing import Any, Callable

from VibeCADCore import VibeCADService, get_service
from VibeCADProvider import (
    AnthropicProvider,
    BaseProvider,
    CodexProvider,
    OfflineProvider,
    ProviderUnavailable,
    provider_tool_schema_digest,
)
from VibeCADIntentMemoryCompiler import compile_intent_memory_update
from VibeCADModelingSurface import (
    CORE_CONVERSATION_VIEW_TOOLS,
    ModelingSurface,
    PROVIDER_READ_TOOL_OWNERS,
    SHARED_CONTEXT_TOOLS,
    infer_engine_from_names,
    resolve_service_surface,
    validate_surface_names,
)
from VibeCADTools import (
    SafetyLevel,
    ToolArgumentValidationError,
    normalize_tool_failure,
    tool_failure,
)
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
PLAN_PROVIDER_SAFE_LEVELS = {
    SafetyLevel.READ,
    SafetyLevel.VIEW,
}
INTERACTION_MODES = frozenset({"build", "plan"})

CORE_PROVIDER_TOOLS = set(CORE_CONVERSATION_VIEW_TOOLS) | set(
    SHARED_CONTEXT_TOOLS
)

def normalize_interaction_mode(value: str | None) -> str:
    clean = str(value or "build").strip().lower()
    if clean not in INTERACTION_MODES:
        raise ValueError(
            f"Unknown VibeCAD interaction mode {clean!r}; expected build or plan."
        )
    return clean


def _provider_safety_levels(interaction_mode: str) -> set[SafetyLevel]:
    return (
        PLAN_PROVIDER_SAFE_LEVELS
        if normalize_interaction_mode(interaction_mode) == "plan"
        else PROVIDER_SAFE_LEVELS
    )

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

ISOLATED_GEOMETRY_TOOLS = {"partdesign.measure"}

SCRIPTED_ENGINE_PROVIDER_TOOLS = {
    "vibescript": VIBESCRIPT_PROVIDER_TOOLS,
}

MAX_TURN_CONTEXT_JSON_BYTES = 256 * 1024
MAX_RECENT_CONVERSATION_TURNS = 16
MAX_RECENT_CONVERSATION_JSON_BYTES = 48 * 1024
MAX_RECENT_CONVERSATION_TURN_CHARACTERS = 6000
MAX_PROVIDER_TOOL_SCHEMAS_JSON_BYTES = 128 * 1024
MAX_VIBESCRIPT_TOOL_SCHEMAS_JSON_BYTES = 16 * 1024


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
            "recompute_pending": bool(
                wait_state.get("recompute_pending", False)
            ),
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
    if provider_name in {"openai", "chatgpt"}:
        return CodexProvider(
            model=service.provider_model(),
            api_key=(
                service.provider_api_key()
                if provider_name == "openai"
                else None
            ),
            auth_mode="api_key" if provider_name == "openai" else "chatgpt",
            reasoning_effort=service.provider_reasoning_effort(),
            base_url=(
                service.provider_base_url()
                if provider_name == "openai"
                else None
            ),
            web_search_enabled=service.web_search_enabled(),
            skills_enabled=service.codex_skills_enabled(),
        )
    if provider_name == "anthropic":
        return AnthropicProvider(
            model=service.provider_model(),
            api_key=service.provider_api_key(),
            reasoning_effort=service.provider_reasoning_effort(),
            base_url=service.provider_base_url(),
            web_search_enabled=service.web_search_enabled(),
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
    reasoning_effort = str(
        getattr(provider, "reasoning_effort", "") or ""
    ).strip()
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
    if not _active_document_exists(service):
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
    interaction_mode: str = "build",
) -> list[str]:
    """Return live-callable names without serializing provider schemas."""

    allowed_safety = _provider_safety_levels(interaction_mode)
    result: list[str] = []
    for name in sorted(_surface_tool_names(service, workbench)):
        tool = service.registry.get(name)
        if tool.safety not in allowed_safety:
            continue
        if not tool.spec.supports_edit_mode(edit_mode):
            continue
        result.append(name)
    return result


def is_provider_safe_tool(
    service: VibeCADService,
    tool_name: str,
    workbench: str | None = None,
    *,
    interaction_mode: str = "build",
) -> bool:
    try:
        tool = service.registry.get(tool_name)
    except KeyError:
        return False
    active = workbench or service.active_workbench_name()
    if tool.safety not in _provider_safety_levels(interaction_mode):
        return False
    if tool_name not in _surface_tool_names(service, active):
        return False
    return tool.spec.supports_edit_mode(_current_edit_mode(service))


def provider_tool_schemas(
    service: VibeCADService,
    workbench: str | None,
    *,
    runtime_state: dict[str, Any] | None = None,
    interaction_mode: str = "build",
) -> list[dict[str, Any]]:
    state = (
        runtime_state
        if runtime_state is not None
        else _minimal_runtime_state(service)
    )
    names = _provider_safe_tool_names(
        service,
        workbench,
        _edit_mode_from_runtime_state(state),
        interaction_mode,
    )
    return [
        _provider_schema_copy(
            service.registry.get(name).to_schema(active_workbench=workbench)
        )
        for name in names
    ]


def _live_provider_surface_state(
    service: VibeCADService,
    interaction_mode: str = "build",
) -> dict[str, Any]:
    """Capture one coherent authorization snapshot on the document thread."""

    workbench = service.active_workbench_name()
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
        "tool_names": _provider_safe_tool_names(
            service,
            workbench,
            _edit_mode_from_runtime_state(runtime_state),
            interaction_mode,
        ),
    }


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
        raise ValueError("The turn-start provider surface has no tools.")
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
            raise ValueError("The requested engine does not match the resolved surface.")
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


def _capture_context_for_provider(
    service: VibeCADService,
    session_trigger: dict[str, Any] | None = None,
    interaction_mode: str = "build",
) -> dict[str, Any]:
    clean_interaction_mode = normalize_interaction_mode(interaction_mode)
    raw_context = service.provider_context_summary()
    # Treat the session boundary as the final model-context allowlist. This
    # prevents any service implementation from accidentally reintroducing broad
    # CAD or domain snapshots.
    allowed_turn_facts = (
        "document",
        "selection",
        "view_screenshot",
        "reference_images",
    )
    context = {
        key: raw_context[key]
        for key in allowed_turn_facts
        if key in raw_context
    }
    workbench = service.active_workbench_name()
    resolution = resolve_service_surface(service, workbench)
    context["workbench"] = workbench
    context["modeling_surface"] = {
        "workbench": str(resolution.workbench or ""),
        "engine": resolution.engine,
        "domain": resolution.domain,
        "surface_id": resolution.surface_id,
        "available": resolution.available,
        **(
            {"unavailable_reason": resolution.unavailable_reason}
            if not resolution.available
            else {}
        ),
    }
    if (
        resolution.engine == "vibescript"
        and resolution.available
        and resolution.domain
    ):
        context["editable_sources"] = (
            vibescript_domains.capture_editable_sources_snapshot(
                service,
                resolution.domain,
            )
        )
    context["_vibecad_debug"] = service.provider_debug_config()
    runtime_state = _minimal_runtime_state(service)
    schemas = provider_tool_schemas(
        service,
        workbench,
        runtime_state=runtime_state,
        interaction_mode=clean_interaction_mode,
    )
    context["provider_tool_schemas"] = schemas
    context["_vibecad_interaction_mode"] = clean_interaction_mode
    try:
        turn_surface = _turn_start_tool_surface(workbench, schemas, resolution=resolution)
    except ValueError as exc:
        if service.provider_name() not in {"openai", "chatgpt"}:
            raise
        context["provider_tool_surface"] = {
            "kind": "unavailable",
            "frozen": True,
            "workbench": str(workbench or ""),
            "reason": str(exc),
        }
    else:
        context["provider_tool_surface"] = turn_surface
    if session_trigger:
        context["session_trigger"] = dict(session_trigger)
    return context


def _complete_context_for_provider(context: Mapping[str, Any]) -> dict[str, Any]:
    """Complete artifact-backed context after leaving the document thread."""

    completed = dict(context)
    editable_sources = completed.get("editable_sources")
    if (
        isinstance(editable_sources, Mapping)
        and editable_sources.get("_vibecad_deferred_vibescript_program_index")
        is True
    ):
        completed["editable_sources"] = (
            vibescript_domains.complete_editable_sources_snapshot(editable_sources)
        )
    return completed


def _context_for_provider(
    service: VibeCADService,
    session_trigger: dict[str, Any] | None = None,
    interaction_mode: str = "build",
) -> dict[str, Any]:
    """Return completed provider context for synchronous compatibility callers."""

    return _complete_context_for_provider(
        _capture_context_for_provider(
            service,
            session_trigger,
            interaction_mode,
        )
    )


def _build_context_for_provider(
    service: VibeCADService,
    session_trigger: dict[str, Any] | None,
    interaction_mode: str,
    document_thread_dispatch: DocumentThreadDispatch | None,
) -> dict[str, Any]:
    captured = _on_document_thread(
        document_thread_dispatch,
        lambda: _capture_context_for_provider(
            service,
            session_trigger,
            interaction_mode,
        ),
    )
    return _complete_context_for_provider(captured)


def _consume_context_view_attachment(
    service: VibeCADService,
    context: Mapping[str, Any],
    dispatch: DocumentThreadDispatch | None,
) -> None:
    """Consume the exact one-shot images already copied into provider context."""

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
    references = context.get("reference_images")
    consume_references = getattr(service, "consume_reference_image_attachments", None)
    if (
        isinstance(references, dict)
        and references.get("images")
        and callable(consume_references)
    ):
        frozen_references = {
            "images": [
                dict(item)
                for item in list(references.get("images") or [])
                if isinstance(item, dict)
            ]
        }
        _on_document_thread(
            dispatch, lambda: consume_references(frozen_references)
        )


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


def _provider_state_payload(context: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "workbench",
        "modeling_surface",
        "document",
        "selection",
        "editable_sources",
    )
    return {
        key: context[key]
        for key in keys
        if key in context and context[key] not in (None, "", [], {})
    }


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
    payload = {"active_state": _provider_state_payload(context)}
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), default=str
    )
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
    return (
        "VIBECAD_CONTEXT_JSON\n"
        + encoded
        + "\nEND_VIBECAD_CONTEXT_JSON\n\n"
        + "RECENT_CONVERSATION_JSON\n"
        + encoded_conversation
        + "\nEND_RECENT_CONVERSATION_JSON\n\n"
        + f"{prompt_section}\n"
        + prompt
    )


def _run_provider(
    provider: BaseProvider,
    prompt: str,
    context: dict[str, Any],
    tool_runner: Callable[[str, str], dict[str, Any]],
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
            f"Sketch {_active_sketch_name(state)} is open for editing. Finish or "
            f"verify that sketch, then call sketcher.close_sketch before running "
            f"{tool.name}."
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
                "Finish and verify the active sketch, then call sketcher.close_sketch."
                if edit_mode == "sketch"
                else "Open the exact target sketch for editing."
            ),
        },
        allowed_values=sorted(tool.spec.edit_modes),
        required_changes=[
            {
                "action": (
                    "call_sketcher.close_sketch"
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
    )

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
                "repair the smallest exact cause. Use vibescript.edit_source for "
                "source-only changes, set_inputs for value-only changes, and "
                "reconfigure_program only for contract or declared-output changes."
            ),
        }

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
            captured = _on_document_thread(
                document_thread_dispatch,
                lambda: capture_inspection_state(service, tool_name, str(args["program_id"])),
            )
            return complete_inspection(captured)
        captured = _on_document_thread(
            document_thread_dispatch,
            lambda: capture_operation_state(service, tool_name, args),
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
        _emit(
            progress_callback,
            {
                "event": "vibescript_domain_worker_started",
                "domain": pack.domain,
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
            },
        )
        execution = adapter.execute_candidate(prepared, cancellation_check=cancellation_check)
        if execution.get("ok") is not True:
            retained = retain_candidate(prepared, status="failed", failure=execution)
            execution["failed_candidate"] = {
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
                "attempt_directory": retained["attempt_directory"],
                "accepted_revision": prepared["accepted_revision_before"],
            }
            execution["model_state"] = candidate_model_state(prepared)
            return execution
        try:
            validated = adapter.validate_result(prepared, execution)
        except DomainRuntimeFailure as exc:
            retained = retain_candidate(prepared, status="validation_failed", failure=exc.payload)
            exc.payload["failed_candidate"] = {
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
                "attempt_directory": retained["attempt_directory"],
                "accepted_revision": prepared["accepted_revision_before"],
            }
            exc.payload["model_state"] = candidate_model_state(prepared)
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
            retained = retain_candidate(prepared, status="validation_failed", failure=failure)
            failure["failed_candidate"] = {
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
                "attempt_directory": retained["attempt_directory"],
                "accepted_revision": prepared["accepted_revision_before"],
            }
            failure["model_state"] = candidate_model_state(prepared)
            return failure
        retain_candidate(prepared, status="validated")
        try:
            publication = _on_document_thread(
                document_thread_dispatch,
                lambda: adapter.publish(service, prepared, validated),
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
            retained = retain_candidate(prepared, status="publication_failed", failure=failure)
            failure["failed_candidate"] = {
                "program_id": prepared["program_id"],
                "revision": prepared["revision"],
                "attempt_directory": retained["attempt_directory"],
                "accepted_revision": prepared["accepted_revision_before"],
            }
            failure["model_state"] = candidate_model_state(prepared)
            return failure
        payload = accept_candidate(prepared, publication)
        payload["source_id"] = str(payload.get("program_id") or prepared["program_id"])
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
        return payload
    except DomainRuntimeFailure as exc:
        return exc.payload
    except Exception as exc:
        return tool_failure(
            tool_name,
            "DOMAIN_LIFECYCLE_FAILED",
            "external_process",
            str(exc),
            requested=args,
            observed={"exception_type": exc.__class__.__name__},
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
            if key == "worker_progress" and isinstance(item, Mapping) and not include_logs:
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
    include_logs: bool = True,
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
    raw_outputs = program.get("live_outputs")
    affected_outputs = []
    live_state = program.get("live_state")
    if isinstance(live_state, Mapping) and isinstance(
        live_state.get("outputs"), list
    ):
        affected_outputs = [
            dict(value)
            for value in live_state["outputs"]
            if isinstance(value, Mapping)
            and str(value.get("name") or "")
            and str(value.get("object_name") or "")
        ]
    elif isinstance(raw_outputs, Mapping):
        affected_outputs = [
            {"name": str(name), **dict(value)}
            for name, value in sorted(
                raw_outputs.items(),
                key=lambda item: str(item[0]),
            )
            if isinstance(value, Mapping)
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
    for key in (
        "latest_candidate",
        "migration_required",
        "migration_reason",
        "migration_action",
    ):
        if program.get(key) not in (None, "", [], {}):
            result[key] = _source_diagnostic_value(
                program[key],
                include_logs=include_logs,
                log_tail_lines=log_tail_lines,
            )
    model_state = inspected.get("model_state")
    if isinstance(model_state, Mapping):
        result["model_state"] = _source_diagnostic_value(
            model_state,
            include_logs=include_logs,
            log_tail_lines=log_tail_lines,
        )
    return result


def _filtered_api_payload(
    tool_name: str,
    description: Mapping[str, Any],
    *,
    names: list[str],
    groups: list[str],
) -> dict[str, Any]:
    """Return either the complete API or a small exact callable selection."""

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
        )
    if not names and not groups:
        result["_vibecad_complete_api_result"] = True
        return result
    selected = set(names)
    for group in groups:
        selected.update(api_groups[group])
    ordered_names = [name for name in by_name if name in selected]
    focused = {
        key: result[key]
        for key in (
            "ok",
            "domain",
            "workbench",
            "program_schema",
            "accepted_output_types",
            "source_globals",
            "result_contract",
            "units",
            "evaluation_model",
            "model_operating_contract",
            "source_value_contract",
            "source_global_contracts",
        )
        if key in result
    }
    focused.update(
        {
            "runtime_exports": [by_name[name] for name in ordered_names],
            "selected_names": ordered_names,
            "selected_groups": groups,
            "api_groups": api_groups,
            "read_more": {
                "tool": "vibescript.read_api",
                "arguments": {"names": ["exact_callable_name"]},
            },
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


def _run_universal_vibescript_tool(
    service: VibeCADService,
    active_workbench: str | None,
    tool_name: str,
    args: dict[str, Any],
    *,
    document_thread_dispatch: DocumentThreadDispatch | None,
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    pack = vibescript_domains.get_vibescript_pack(active_workbench)
    if pack is None:
        return tool_failure(
            tool_name,
            "DOMAIN_UNAVAILABLE",
            "surface",
            "The active workbench has no VibeScript source domain.",
            requested=args,
        )
    if tool_name == "vibescript.read_api":
        from VibeCADVibeScriptDomainRuntime import describe_api

        return _filtered_api_payload(
            tool_name,
            describe_api(pack),
            names=[str(value) for value in list(args.get("names") or [])],
            groups=[str(value) for value in list(args.get("groups") or [])],
        )
    if tool_name == "vibescript.read_source":
        from VibeCADVibeScriptDomainRuntime import (
            DomainRuntimeFailure,
            capture_inspection_state,
            complete_inspection,
        )

        source_id = str(args["source_id"])
        try:
            captured = _on_document_thread(
                document_thread_dispatch,
                lambda: capture_inspection_state(
                    service,
                    f"vibescript.{pack.domain}.inspect_program",
                    source_id,
                ),
            )
            return _read_source_payload(
                complete_inspection(captured),
                line_start=args.get("line_start"),
                line_end=args.get("line_end"),
                include_logs=bool(args.get("include_logs", True)),
                log_tail_lines=args.get("log_tail_lines"),
            )
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
    if tool_name == "vibescript.build_program":
        from VibeCADVibeScriptDomainRuntime import (
            DomainRuntimeFailure,
            capture_inspection_state,
            complete_inspection,
        )

        source_id = str(args["source_id"])
        expected_revision = str(args["expected_revision"])
        try:
            captured = _on_document_thread(
                document_thread_dispatch,
                lambda: capture_inspection_state(
                    service,
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
                                "source_id": source_id,
                                "include_logs": False,
                            },
                        }
                    ],
                )
            result = _run_domain_vibescript_tool(
                service,
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
            return result
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
        result = _run_domain_vibescript_tool(
            service,
            f"vibescript.{pack.domain}.edit_source",
            {
                "program_id": str(args["source_id"]),
                "expected_revision": str(args["expected_revision"]),
                "source": str(args["source"]),
            },
            document_thread_dispatch=document_thread_dispatch,
            cancellation_check=cancellation_check,
            progress_callback=progress_callback,
        )
        if result.get("program_id"):
            result["source_id"] = str(result["program_id"])
        return result
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
    return accept_candidate(prepared, publication)


def make_provider_tool_runner(
    service: VibeCADService,
    *,
    tool_trace: list[dict[str, Any]],
    progress_callback: ProgressCallback | None,
    cancellation_check: CancellationCheck | None,
    steering_check: SteeringCheck | None,
    question_callback: QuestionCallback | None,
    session_trigger: dict[str, Any] | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
    turn_surface: dict[str, Any] | None = None,
    turn_schemas: list[dict[str, Any]] | None = None,
    turn_modeling_surface: dict[str, Any] | None = None,
    interaction_mode: str = "build",
):
    clean_interaction_mode = normalize_interaction_mode(interaction_mode)
    frozen_schemas = json.loads(json.dumps(turn_schemas or []))
    frozen_modeling_surface = json.loads(json.dumps(turn_modeling_surface or {}))

    def run(tool_name: str, arguments_json: str = "{}") -> dict[str, Any]:
        started = time.monotonic()
        tool = None
        args: dict[str, Any] = {}

        def finalize(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal args, tool
            if not bool(payload.get("ok")):
                payload = normalize_tool_failure(tool_name, args, payload)
            elif tool_name not in {
                "vibescript.read_source",
                "vibescript.read_api",
            }:
                _on_document_thread(
                    document_thread_dispatch,
                    lambda: service.note_provider_tool_targets(args, payload),
                )
            trace_payload = dict(payload)
            trace_payload.pop("_vibecad_image_attachment", None)
            trace_payload.pop("_vibecad_complete_source_result", None)
            trace_payload.pop("_vibecad_complete_api_result", None)
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
        live_surface = _on_document_thread(
            document_thread_dispatch,
            lambda: _live_provider_surface_state(
                service, clean_interaction_mode
            ),
        )
        active_workbench = live_surface["workbench"]
        runtime_state = live_surface["runtime_state"]
        visible_names = live_surface["tool_names"]
        if isinstance(turn_surface, dict):
            expected_tuple = {
                "workbench": str(turn_surface.get("workbench") or ""),
                "engine": str(turn_surface.get("engine") or ""),
                "surface_id": str(turn_surface.get("surface_id") or ""),
            }
            observed_tuple = {
                "workbench": str(active_workbench or ""),
                "engine": str(live_surface.get("engine") or ""),
                "surface_id": str(live_surface.get("surface_id") or ""),
            }
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
                            "turn_start": expected_tuple,
                            "live": observed_tuple,
                            "unavailable_reason": live_surface.get("unavailable_reason"),
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
            from VibeCADDesignReview import run_design_review

            review_context = _build_context_for_provider(
                service,
                session_trigger,
                clean_interaction_mode,
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
        if tool_name == "component_catalog.search":
            from tool_impl.service.component_catalog_search import capture, complete

            idle_state = _wait_for_document_idle(
                service,
                document_thread_dispatch,
                cancellation_check,
                progress_callback,
            )
            if not idle_state.get("ok"):
                return finalize(_document_idle_failure(tool_name, args, idle_state))
            try:
                captured = _on_document_thread(
                    document_thread_dispatch,
                    lambda: capture(service),
                )
                payload = complete(captured, **args)
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
            "vibescript.read_source",
            "vibescript.read_api",
            "vibescript.build_program",
            "vibescript.edit_source",
        }:
            return finalize(
                _run_universal_vibescript_tool(
                    service,
                    active_workbench,
                    tool_name,
                    args,
                    document_thread_dispatch=document_thread_dispatch,
                    cancellation_check=cancellation_check,
                    progress_callback=progress_callback,
                )
            )
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
        if tool_name in ISOLATED_GEOMETRY_TOOLS:
            from VibeCADGeometry import execute_job
            from tool_impl.service.partdesign_measure import (
                cleanup_isolated_measurement,
                finish_isolated_measurement,
                prepare_isolated_measurement,
            )

            prepared = _on_document_thread(
                document_thread_dispatch,
                lambda: prepare_isolated_measurement(service, args["measurement"]),
            )
            if prepared.get("mode") == "immediate":
                return finalize(dict(prepared["payload"]))
            _emit(
                progress_callback,
                {
                    "event": "geometry_worker_started",
                    "operation": "minimum_distance",
                    "input_complexity": prepared.get("input_complexity"),
                },
            )
            try:
                execution = execute_job(
                    prepared["request_path"],
                    prepared["result_path"],
                    cancellation_check=cancellation_check,
                )
                payload = finish_isolated_measurement(prepared, execution)
            finally:
                cleanup_isolated_measurement(prepared)
            return finalize(payload)
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
            clean_interaction_mode,
            document_thread_dispatch,
        )
        completed = refreshed
        _consume_context_view_attachment(
            service, completed, document_thread_dispatch
        )
        if not isinstance(turn_surface, dict):
            return completed

        live_surface = dict(completed.get("provider_tool_surface") or {})
        expected_tuple = (
            str(turn_surface.get("workbench") or ""),
            str(turn_surface.get("engine") or ""),
            str(turn_surface.get("surface_id") or ""),
        )
        live_tuple = (
            str(live_surface.get("workbench") or ""),
            str(live_surface.get("engine") or ""),
            str(live_surface.get("surface_id") or ""),
        )
        completed["provider_tool_surface"] = dict(turn_surface)
        completed["provider_tool_schemas"] = json.loads(json.dumps(frozen_schemas))
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
    session_trigger: dict[str, Any] | None,
    persist_input_as_user: bool,
    prompt_section: str,
    document_thread_dispatch: DocumentThreadDispatch | None,
    interaction_mode: str,
) -> VibeCADResponse:
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Prompt cannot be empty.")
    clean_interaction_mode = normalize_interaction_mode(interaction_mode)
    active_service = service or _on_document_thread(
        document_thread_dispatch,
        get_service,
    )
    persistence = _on_document_thread(
        document_thread_dispatch,
        active_service.document_persistence_state,
    )
    if not persistence.get("enabled"):
        raise RuntimeError(
            str(
                persistence.get("message")
                or "Save the active document to enable VibeCAD."
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
        clean_interaction_mode,
        document_thread_dispatch,
    )
    _consume_context_view_attachment(
        active_service, context, document_thread_dispatch
    )
    tool_trace: list[dict[str, Any]] = []
    _emit(
        progress_callback,
        {
            "event": "context_build_completed",
            "workbench": context.get("workbench"),
            "provider_tool_count": len(context.get("provider_tool_schemas") or []),
        },
    )
    active_provider = provider or _on_document_thread(
        document_thread_dispatch,
        lambda: choose_provider(
            active_service,
            prefer_online=prefer_online,
        ),
    )
    if clean_interaction_mode == "plan" and not isinstance(
        active_provider, CodexProvider
    ):
        raise ProviderUnavailable("Plan mode requires an OpenAI Codex provider.")
    provider_name = active_provider.__class__.__name__
    provider_runtime = provider_execution_identity(active_provider)
    provider_runtime["interaction_mode"] = clean_interaction_mode
    tool_runner = make_provider_tool_runner(
        active_service,
        tool_trace=tool_trace,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        steering_check=steering_check,
        question_callback=question_callback,
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
        turn_modeling_surface=(
            dict(context["modeling_surface"])
            if isinstance(context.get("modeling_surface"), dict)
            else None
        ),
        interaction_mode=clean_interaction_mode,
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
            _provider_prompt(
                clean_prompt,
                context,
                prompt_section=prompt_section,
                recent_conversation=turn_conversation,
                current_user_message=clean_prompt if persist_input_as_user else None,
            ),
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
            clean_interaction_mode,
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
            clean_interaction_mode,
            document_thread_dispatch,
        )
        return VibeCADResponse(
            provider=provider_name,
            final_output=final_output,
            context=failed_context,
            tool_trace=tool_trace,
            error=str(exc),
        )


def run_prompt(
    prompt: str,
    service: VibeCADService | None = None,
    prefer_online: bool = True,
    provider: BaseProvider | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    steering_check: SteeringCheck | None = None,
    question_callback: QuestionCallback | None = None,
    document_thread_dispatch: DocumentThreadDispatch | None = None,
    interaction_mode: str = "build",
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
        session_trigger=None,
        persist_input_as_user=True,
        prompt_section="CURRENT_USER_MESSAGE",
        document_thread_dispatch=document_thread_dispatch,
        interaction_mode=interaction_mode,
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
        session_trigger=clean_event,
        persist_input_as_user=False,
        prompt_section="CURRENT_SESSION_EVENT",
        document_thread_dispatch=document_thread_dispatch,
        interaction_mode="build",
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
