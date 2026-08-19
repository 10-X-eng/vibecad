# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one document-bound dispatcher from a frozen Native provider turn."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import secrets
from typing import Any, Callable, Mapping

from VibeCADNativeDispatch import NativeDispatchError, NativeTurnDispatcher
from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeInput import NativeInputAuthorizer
from VibeCADNativeOutput import NativeOutputAuthorizer
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeTargets import document_uid
from VibeCADNativeTurn import (
    NativeTurnSnapshot,
    freeze_native_turn,
    require_frozen_native_turn,
)
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


@dataclass(slots=True)
class NativeSessionExecution:
    dispatcher: NativeTurnDispatcher
    turn: NativeTurnSnapshot
    undo_ledger: NativeAssistantUndoLedger
    run_id: str

    def close(self) -> None:
        self.undo_ledger.end_run(self.run_id)


def _edit_or_task_active(service: Any) -> bool:
    summary = service.task_panel_summary()
    if not isinstance(summary, Mapping):
        return False
    if summary.get("active_dialog"):
        return True
    if not summary.get("edit_mode"):
        return False
    if summary.get("active_sketch"):
        return True
    edit_object = summary.get("edit_object")
    return not (
        isinstance(edit_object, Mapping)
        and str(edit_object.get("type") or "") == "Assembly::AssemblyObject"
    )


def _validate_expected_turn(
    expected_surface: Mapping[str, Any],
    expected_schemas: list[dict[str, Any]],
    turn: NativeTurnSnapshot,
) -> None:
    if (
        expected_surface.get("kind") != "turn_start_snapshot"
        or expected_surface.get("frozen") is not True
        or expected_surface.get("engine") != "native"
    ):
        raise NativeDispatchError(
            "NATIVE_TURN_INVALID",
            "The provider did not supply one frozen Native turn.",
        )
    expected_names = tuple(str(value) for value in expected_surface.get("tool_names") or [])
    try:
        encoded = json.dumps(
            expected_schemas,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise NativeDispatchError(
            "NATIVE_TURN_INVALID",
            "The frozen Native schemas are not JSON.",
        ) from exc
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    visible_turn_schemas = [
        provider_visible_native_schema(schema) for schema in turn.provider_schemas
    ]
    visible_turn_encoded = json.dumps(
        visible_turn_schemas,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    visible_turn_digest = hashlib.sha256(
        visible_turn_encoded.encode("utf-8")
    ).hexdigest()
    if (
        expected_names != turn.tool_names
        or expected_surface.get("schema_count") != len(expected_schemas)
        or expected_surface.get("schema_sha256") != digest
        or digest not in {turn.schema_sha256, visible_turn_digest}
        or str(expected_surface.get("domain") or "") != turn.surface.surface_id
        or str(expected_surface.get("surface_id") or "")
        != turn.surface.modeling_surface_id
    ):
        raise NativeDispatchError(
            "NATIVE_TURN_CHANGED",
            "The Native ribbon contract changed before dispatch was created.",
        )


def create_native_session_execution(
    *,
    service: Any,
    expected_surface: Mapping[str, Any],
    expected_schemas: list[dict[str, Any]],
    debug_sink: Callable[[Mapping[str, Any]], None] | None = None,
    registry: Any | None = None,
    controller: Any | None = None,
    output_authorizer: NativeOutputAuthorizer | None = None,
    input_authorizer: NativeInputAuthorizer | None = None,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None,
) -> NativeSessionExecution:
    if str(service.modeling_engine() or "").strip().lower() != "native":
        raise NativeDispatchError(
            "NATIVE_AUTHORITY_CHANGED",
            "The document is no longer under Native authority.",
        )
    document = service._active_document()
    if document is None:
        raise NativeDispatchError(
            "NATIVE_DOCUMENT_CHANGED",
            "No exact active Native document is available.",
        )
    selected_registry = registry or build_native_capability_registry()
    turn = freeze_native_turn(controller, selected_registry)
    _validate_expected_turn(expected_surface, expected_schemas, turn)
    state = service.native_document_state_store()
    uid = document_uid(document)
    state.ensure_document(uid)
    authority = state.snapshot(uid).get("native_authority")
    if not isinstance(authority, Mapping) or authority.get("active") is not True:
        raise NativeDispatchError(
            "NATIVE_AUTHORITY_CHANGED",
            "Native mutation authority is not active for the exact document.",
        )

    def reauthorize() -> NativeTurnSnapshot:
        if str(service.modeling_engine() or "").strip().lower() != "native":
            raise NativeDispatchError(
                "NATIVE_AUTHORITY_CHANGED",
                "The document is no longer under Native authority.",
            )
        if service._active_document() is not document:
            raise NativeDispatchError(
                "NATIVE_DOCUMENT_CHANGED",
                "The exact Native document is no longer active.",
            )
        return require_frozen_native_turn(turn, controller, selected_registry)

    run_id = secrets.token_hex(16)
    undo = service.native_assistant_undo_ledger()
    if not isinstance(undo, NativeAssistantUndoLedger):
        raise NativeDispatchError(
            "NATIVE_UNDO_UNAVAILABLE",
            "The Native host has no assistant undo provenance store.",
        )
    undo.begin_run(run_id)
    background_manager_factory = getattr(service, "native_background_manager", None)
    context = NativeRuntimeContext(
        service=service,
        document=document,
        state=state,
        undo_ledger=undo,
        reauthorize_turn=reauthorize,
        active_document=service._active_document,
        active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
        edit_or_task_active=lambda: _edit_or_task_active(service),
        authorize_output=output_authorizer,
        authorize_input=input_authorizer,
        background_manager=(
            background_manager_factory()
            if callable(background_manager_factory)
            else None
        ),
        document_thread_dispatch=document_thread_dispatch,
    )
    dispatcher = NativeTurnDispatcher(
        document=document,
        state=state,
        registry=selected_registry,
        turn=turn,
        runtimes=build_native_runtime_bindings(context, turn.tool_names),
        reauthorize_turn=reauthorize,
        active_document=service._active_document,
        debug_sink=debug_sink,
    )
    return NativeSessionExecution(dispatcher, turn, undo, run_id)
