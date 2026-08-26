# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one document-bound dispatcher from a frozen Native provider turn."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import secrets
from typing import Any, Callable, Mapping

from VibeCADNativeDispatch import NativeDispatchError, NativeTurnDispatcher
from VibeCADNativeCapabilityRegistry import (
    _provider_schema_operations,
    provider_visible_native_schema,
)
from VibeCADNativeInput import NativeInputAuthorizer
from VibeCADModelingSurface import NATIVE_DERIVED_ARTIFACT_SURFACES
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
    authority_release: Callable[[], None] | None = None
    background_manager: Any | None = None
    document_uid: str = ""
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.undo_ledger.end_run(self.run_id)
        finally:
            if self.authority_release is not None:
                self.authority_release()


def _edit_or_task_active(service: Any) -> bool:
    summary = service.task_panel_summary()
    if not isinstance(summary, Mapping):
        return False
    if not summary.get("edit_mode"):
        return bool(summary.get("active_dialog"))
    if summary.get("active_sketch"):
        return True
    edit_object = summary.get("edit_object")
    if (
        isinstance(edit_object, Mapping)
        and str(edit_object.get("type") or "") == "Assembly::AssemblyObject"
    ):
        return False
    return True


def _validate_expected_turn(
    expected_surface: Mapping[str, Any],
    expected_schemas: list[dict[str, Any]],
    turn: NativeTurnSnapshot,
    expected_authorization: Mapping[str, Any] | None = None,
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
    changed = []
    if expected_names != turn.tool_names:
        changed.append("tool names")
    if expected_surface.get("schema_count") != len(expected_schemas):
        changed.append("schema count")
    if expected_surface.get("schema_sha256") != digest:
        changed.append("captured schema digest")
    if digest not in {turn.schema_sha256, visible_turn_digest}:
        expected_by_name = {
            str(schema.get("name") or ""): schema for schema in expected_schemas
        }
        turn_by_name = {
            str(schema.get("name") or ""): schema for schema in visible_turn_schemas
        }
        mismatched = sorted(
            name
            for name in set(expected_by_name) | set(turn_by_name)
            if expected_by_name.get(name) != turn_by_name.get(name)
        )
        changed.append(
            "registry schema digest"
            + (f" ({', '.join(mismatched)})" if mismatched else "")
        )
    if expected_authorization is not None:
        if (
            str(expected_authorization.get("schema_sha256") or "")
            != visible_turn_digest
        ):
            changed.append("operation authorization digest")
        expected_operations = expected_authorization.get("operations_by_tool")
        if not isinstance(expected_operations, Mapping):
            changed.append("operation authorization map")
        else:
            frozen_operations = {
                str(schema.get("name") or ""): list(operations)
                for schema in turn.provider_schemas
                if (operations := _provider_schema_operations(schema))
            }
            if {
                str(name): [str(operation) for operation in operations]
                for name, operations in expected_operations.items()
                if isinstance(operations, (list, tuple))
            } != frozen_operations:
                changed.append("operation authorization scope")
    if str(expected_surface.get("domain") or "") != turn.surface.surface_id:
        changed.append("ribbon domain")
    if (
        str(expected_surface.get("surface_id") or "")
        != turn.surface.modeling_surface_id
    ):
        changed.append("ribbon identity")
    if changed:
        raise NativeDispatchError(
            "NATIVE_TURN_CHANGED",
            "The Native ribbon contract changed before dispatch was created: "
            + ", ".join(changed)
            + ".",
        )


def create_native_session_execution(
    *,
    service: Any,
    expected_surface: Mapping[str, Any],
    expected_schemas: list[dict[str, Any]],
    expected_authorization: Mapping[str, Any] | None = None,
    debug_sink: Callable[[Mapping[str, Any]], None] | None = None,
    registry: Any | None = None,
    controller: Any | None = None,
    output_authorizer: NativeOutputAuthorizer | None = None,
    input_authorizer: NativeInputAuthorizer | None = None,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None,
) -> NativeSessionExecution:
    authoring_engine = str(service.modeling_engine() or "").strip().lower()
    requested_surface_id = str(expected_surface.get("domain") or "")
    scoped_surface_id = (
        requested_surface_id
        if (
            authoring_engine == "vibescript"
            and requested_surface_id in NATIVE_DERIVED_ARTIFACT_SURFACES
        )
        else None
    )
    if authoring_engine != "native" and scoped_surface_id is None:
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
    expected_names = tuple(
        str(value) for value in expected_surface.get("tool_names") or ()
    )
    authorized_operations = None
    if expected_authorization is not None:
        if not isinstance(expected_authorization, Mapping) or not isinstance(
            expected_authorization.get("operations_by_tool"), Mapping
        ):
            raise NativeDispatchError(
                "NATIVE_TURN_INVALID",
                "The captured turn has invalid Native operation authorization.",
            )
        authorized_operations = expected_authorization["operations_by_tool"]
    turn = freeze_native_turn(
        controller=controller,
        registry=selected_registry,
        tool_names=expected_names,
        provider_schemas=tuple(expected_schemas),
        authorized_operations=authorized_operations,
    )
    _validate_expected_turn(
        expected_surface,
        expected_schemas,
        turn,
        expected_authorization,
    )
    state = service.native_document_state_store()
    uid = document_uid(document)
    state.ensure_document(uid)
    if authoring_engine == "native":
        authority = state.snapshot(uid).get("native_authority")
        if not isinstance(authority, Mapping) or authority.get("active") is not True:
            raise NativeDispatchError(
                "NATIVE_AUTHORITY_CHANGED",
                "Native mutation authority is not active for the exact document.",
            )
    elif turn.surface.surface_id != scoped_surface_id:
        raise NativeDispatchError(
            "NATIVE_AUTHORITY_CHANGED",
            "The frozen ribbon cannot use scoped Native authority.",
        )

    def reauthorize() -> NativeTurnSnapshot:
        if str(service.modeling_engine() or "").strip().lower() != authoring_engine:
            raise NativeDispatchError(
                "NATIVE_AUTHORITY_CHANGED",
                "The document authoring authority changed during this turn.",
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
    scope_token = (
        state.begin_scoped_authority(
            uid,
            scoped_surface_id,
            exact_capabilities=("document.undo",),
        )
        if scoped_surface_id is not None
        else None
    )
    background_manager_factory = getattr(service, "native_background_manager", None)
    background_manager = (
        background_manager_factory()
        if callable(background_manager_factory)
        else None
    )
    try:
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
            background_manager=background_manager,
            document_thread_dispatch=document_thread_dispatch,
            run_id=run_id,
            scoped_capability_prefix=scoped_surface_id,
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
    except Exception:
        undo.end_run(run_id)
        if scope_token is not None:
            state.end_scoped_authority(uid, scope_token)
        raise
    release = (
        (lambda: state.end_scoped_authority(uid, scope_token))
        if scope_token is not None
        else None
    )
    return NativeSessionExecution(
        dispatcher,
        turn,
        undo,
        run_id,
        release,
        background_manager,
        uid,
    )
