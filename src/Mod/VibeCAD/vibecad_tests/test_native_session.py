# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

import VibeCADNativeSessionFactory as factory_module
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    _provider_schema_operations,
)
from VibeCADNativeCommonSchema import common_capability_definitions
from VibeCADNativeProviderRunner import NativeProviderToolRunner
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSessionFactory import (
    NativeSessionExecution,
    create_native_session_execution,
)
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "document-a"
    Name = "DocumentA"


def _common_turn(surface_id="model"):
    definitions = common_capability_definitions()
    schemas = tuple(
        definition.provider_schema(
            tuple(variant.operation for variant in definition.variants)
        )
        for definition in definitions
    )
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            surface_id,
            9,
            "a" * 64,
            ("VibeCAD_Test",),
            ("VibeCAD_Test",),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=tuple(definition.name for definition in definitions),
        schemas=schemas,
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    turn = NativeTurnSnapshot.from_provider_surface(surface)
    schema_list = [dict(value) for value in schemas]
    digest = hashlib.sha256(
        json.dumps(
            schema_list,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    frozen = {
        "kind": "turn_start_snapshot",
        "frozen": True,
        "workbench": (
            "FemWorkbench" if surface_id == "analyze" else "PartDesignWorkbench"
        ),
        "engine": "native",
        "domain": surface_id,
        "surface_id": turn.surface.modeling_surface_id,
        "available": True,
        "unavailable_reason": "",
        "tool_names": list(turn.tool_names),
        "schema_count": len(schema_list),
        "schema_sha256": digest,
    }
    return turn, schema_list, frozen


class _Service:
    def __init__(self, mode="native") -> None:
        self.document = _Document()
        self.state = NativeDocumentStateStore()
        if mode == "native":
            self.state.begin_native_authority(self.document.Uid)
        self.undo = NativeAssistantUndoLedger()
        self.mode = mode

    def modeling_engine(self):
        return self.mode

    def _active_document(self):
        return self.document

    def native_document_state_store(self):
        return self.state

    def native_assistant_undo_ledger(self):
        return self.undo

    def task_panel_summary(self):
        return {"active_dialog": False, "edit_mode": False}


def test_session_factory_binds_only_the_exact_frozen_common_surface(monkeypatch) -> None:
    turn, schemas, frozen = _common_turn()
    monkeypatch.setattr(
        factory_module,
        "freeze_native_turn",
        lambda *_args, **_kwargs: turn,
    )
    monkeypatch.setattr(
        factory_module,
        "require_frozen_native_turn",
        lambda expected, *_args: expected,
    )
    service = _Service()

    execution = create_native_session_execution(
        service=service,
        expected_surface=frozen,
        expected_schemas=schemas,
        registry=build_native_capability_registry(),
    )

    assert execution.turn == turn
    assert execution.dispatcher.call_count == 0
    assert len(execution.run_id) == 32
    assert execution.undo_ledger is service.undo
    execution.close()

    next_execution = create_native_session_execution(
        service=service,
        expected_surface=frozen,
        expected_schemas=schemas,
        registry=build_native_capability_registry(),
    )
    assert next_execution.undo_ledger is execution.undo_ledger
    assert next_execution.run_id != execution.run_id
    next_execution.close()


def test_session_factory_uses_captured_analyze_schema_without_rereading_lifecycle(
    monkeypatch,
) -> None:
    turn, schemas, frozen = _common_turn("analyze")
    monkeypatch.setattr(
        factory_module,
        "freeze_native_turn",
        lambda *_args, **_kwargs: turn,
    )
    monkeypatch.setattr(
        factory_module,
        "require_frozen_native_turn",
        lambda expected, *_args: expected,
    )
    service = _Service()

    def lifecycle_changed_after_capture():
        raise AssertionError("Analyze lifecycle was read after the turn was captured")

    service.native_active_snapshot = lifecycle_changed_after_capture

    execution = create_native_session_execution(
        service=service,
        expected_surface=frozen,
        expected_schemas=schemas,
        registry=build_native_capability_registry(),
    )
    execution.close()


def test_session_factory_passes_internal_operation_authorization_to_turn_freeze(
    monkeypatch,
) -> None:
    turn, schemas, frozen = _common_turn("analyze")
    captured = {}

    def freeze(*_args, **kwargs):
        captured.update(kwargs)
        return turn

    monkeypatch.setattr(factory_module, "freeze_native_turn", freeze)
    monkeypatch.setattr(
        factory_module,
        "require_frozen_native_turn",
        lambda expected, *_args: expected,
    )
    authorization = {
        "schema_sha256": turn.schema_sha256,
        "operations_by_tool": {
            schema["name"]: list(_provider_schema_operations(schema))
            for schema in turn.provider_schemas
        },
    }

    execution = create_native_session_execution(
        service=_Service(),
        expected_surface=frozen,
        expected_schemas=schemas,
        expected_authorization=authorization,
        registry=build_native_capability_registry(),
    )

    assert captured["authorized_operations"] == authorization["operations_by_tool"]
    execution.close()


def test_session_factory_refuses_schema_or_authority_drift(monkeypatch) -> None:
    turn, schemas, frozen = _common_turn()
    monkeypatch.setattr(
        factory_module,
        "freeze_native_turn",
        lambda *_args, **_kwargs: turn,
    )
    service = _Service()

    changed = [dict(value) for value in schemas]
    changed[0] = {**changed[0], "description": "Changed after freeze."}
    with pytest.raises(Exception, match="contract changed"):
        create_native_session_execution(
            service=service,
            expected_surface=frozen,
            expected_schemas=changed,
            registry=build_native_capability_registry(),
        )

    changed_surface = {**frozen, "surface_id": frozen["surface_id"] + "-stale"}
    with pytest.raises(Exception, match="contract changed"):
        create_native_session_execution(
            service=service,
            expected_surface=changed_surface,
            expected_schemas=schemas,
            registry=build_native_capability_registry(),
        )

    service.mode = "vibescript"
    with pytest.raises(Exception, match="no longer under Native"):
        create_native_session_execution(
            service=service,
            expected_surface=frozen,
            expected_schemas=schemas,
            registry=build_native_capability_registry(),
        )


def test_analyze_session_scopes_native_calls_under_vibescript_authority(
    monkeypatch,
) -> None:
    turn, schemas, frozen = _common_turn("analyze")
    monkeypatch.setattr(
        factory_module,
        "freeze_native_turn",
        lambda *_args, **_kwargs: turn,
    )
    monkeypatch.setattr(
        factory_module,
        "require_frozen_native_turn",
        lambda expected, *_args: expected,
    )
    service = _Service("vibescript")

    execution = create_native_session_execution(
        service=service,
        expected_surface=frozen,
        expected_schemas=schemas,
        registry=build_native_capability_registry(),
    )

    assert service.modeling_engine() == "vibescript"
    authority = service.state.snapshot(service.document.Uid)["native_authority"]
    assert authority["active"] is False
    ticket = service.state.begin_call(service.document.Uid, "analyze.model")
    service.state.authorize_mutation(ticket)

    execution.close()

    service.state.complete_mutation(ticket, {"ok": True})
    assert service.state.snapshot(service.document.Uid)["recent_receipts"] == []


class _Dispatcher:
    def __init__(self, result=None) -> None:
        self.calls = []
        self.result = result or {"ok": True, "value": 4}

    def call(self, name, arguments, call_id):
        self.calls.append((name, arguments, call_id))
        return dict(self.result)


class _Ledger:
    def __init__(self) -> None:
        self.ended = []

    def end_run(self, run_id):
        self.ended.append(run_id)


def _provider_runner(*, changed=False, scope_changed=False, cancelled=False, result=None):
    _turn, schemas, frozen = _common_turn()
    dispatcher = _Dispatcher(result)
    ledger = _Ledger()
    execution = NativeSessionExecution(dispatcher, SimpleNamespace(), ledger, "run-a")
    live = dict(frozen)
    if changed:
        live["surface_id"] = "vibecad/surface/native/mesh/10/bbbbbbbbbbbb"
    if scope_changed:
        live["schema_sha256"] = "b" * 64
    events = []
    traces = []
    context = {
        "provider_tool_surface": live,
        "provider_tool_schemas": schemas,
        "modeling_surface": {"engine": "native", "domain": "model"},
        "native_state": {"surface_id": "model", "structural_revision": 4},
    }
    runner = NativeProviderToolRunner(
        execution=execution,
        document_dispatch=lambda operation: operation(),
        refresh_context=lambda: context,
        frozen_surface=frozen,
        frozen_schemas=schemas,
        frozen_modeling_surface={"engine": "native", "domain": "model"},
        tool_trace=traces,
        progress_callback=events.append,
        cancellation_check=lambda: cancelled,
    )
    return runner, dispatcher, ledger, traces, events


def test_provider_runner_dispatches_call_id_and_records_concise_trace() -> None:
    runner, dispatcher, _ledger, traces, events = _provider_runner()

    result = runner("state.read", '{"operation":"active"}', "provider-call-1")

    assert result == {
        "ok": True,
        "value": 4,
        "_vibecad_native_result": True,
    }
    assert dispatcher.calls == [
        ("state.read", '{"operation":"active"}', "provider-call-1")
    ]
    assert traces[0]["tool_name"] == "state.read"
    assert "arguments" not in traces[0]
    assert [event["event"] for event in events] == [
        "native_tool_started",
        "native_tool_completed",
    ]


def test_provider_runner_reports_only_a_successful_exact_turn_transition() -> None:
    ordinary, *_rest = _provider_runner()
    assert ordinary.turn_transition_requested() is False
    ordinary("state.read", '{"operation":"active"}', "provider-call-1")
    assert ordinary.turn_transition_requested() is False

    transition, *_rest = _provider_runner(
        result={"ok": True, "next_turn_required": True}
    )
    transition("sketch.open", "{}", "provider-call-2")
    assert transition.turn_transition_requested() is True


def test_provider_runner_starts_a_new_loop_when_same_ribbon_scope_changes() -> None:
    runner, _dispatcher, _ledger, traces, _events = _provider_runner(
        scope_changed=True,
    )

    result = runner("analyze.model", '{"operation":"create_analysis"}', "create")

    assert result["ok"] is True
    assert result["next_turn_required"] is True
    assert result["next_surface"] == "model"
    assert result["provider_surface_changed"] is True
    assert traces[-1]["result"]["next_turn_required"] is True
    assert runner.turn_transition_requested() is True


def test_completed_background_mutation_starts_with_fresh_native_state() -> None:
    runner, _dispatcher, _ledger, traces, _events = _provider_runner(
        result={
            "ok": True,
            "job": {
                "job_id": "a" * 32,
                "phase": "completed",
                "terminal": True,
                "document_changed": True,
            },
        }
    )

    result = runner("native.job", '{"operation":"status"}', "status")

    assert result["provider_surface_changed"] is True
    assert result["next_turn_required"] is True
    assert result["next_surface"] == "model"
    assert traces[-1]["result"]["next_turn_required"] is True
    assert runner.turn_transition_requested() is True


def test_provider_update_keeps_state_only_while_frozen_surface_is_live() -> None:
    current, *_rest = _provider_runner()
    current_context = current.provider_update()
    assert current_context["native_state"]["structural_revision"] == 4
    assert current_context["modeling_surface"].get("invalidated") is not True

    changed, *_rest = _provider_runner(changed=True)
    changed_context = changed.provider_update()
    assert "native_state" not in changed_context
    assert changed_context["modeling_surface"]["invalidated"] is True
    assert changed_context["modeling_surface"]["next_turn_required"] is True


def test_cancel_and_close_do_not_dispatch_or_touch_later_calls() -> None:
    cancelled, dispatcher, ledger, traces, _events = _provider_runner(cancelled=True)
    result = cancelled("state.read", "{}", "provider-call-1")
    assert result["error_code"] == "NATIVE_RUN_CANCELLED"
    assert dispatcher.calls == []
    assert traces == []

    cancelled.close()
    cancelled.close()
    assert ledger.ended == ["run-a"]
    assert cancelled("state.read", "{}", "provider-call-2")["error_code"] == (
        "NATIVE_TURN_CLOSED"
    )
