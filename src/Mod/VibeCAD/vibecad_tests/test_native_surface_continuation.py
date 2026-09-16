# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace
import json

import VibeCADSession as session_module


def test_continuation_carries_actual_tool_outcomes_without_claiming_completion(monkeypatch):
    captured = {}

    def run_turn(prompt, **kwargs):
        captured.update(prompt=prompt, **kwargs)
        return SimpleNamespace(final_output="continued")

    monkeypatch.setattr(session_module, "_run_session_turn", run_turn)
    trace = [
        {"tool_name": "sheet_metal.inspect", "result": {
            "ok": True, "objects": ["Shell", "Drawer", "Drawer001", "Drawer002"]}},
        {"tool_name": "sheet_metal.edit", "result": {
            "ok": False, "error_code": "INVALID_ARGUMENT", "message": "No geometry changed"}},
        {"tool_name": "sheet_metal.edit", "result": {
            "ok": True, "status": "pending", "receipt_id": "pending-1"}},
        {"tool_name": "workspace.switch", "result": {
            "ok": True, "workspace": "modeling", "next_turn_required": True}},
    ]
    session_module.run_native_surface_continuation({
        "type": "cad_workspace_changed", "document_uid": "document-a",
        "document_name": "Design", "surface_id": "model", "workspace": "modeling",
        "tool_trace": trace,
    })
    encoded = captured["prompt"].split("RECENT_TOOL_ACTIVITY_JSON\n", 1)[1]
    activity = json.loads(encoded.split("\nEND_RECENT_TOOL_ACTIVITY_JSON", 1)[0])
    assert activity["calls"] == trace
    assert activity["omitted_call_count"] == 0
    assert "current document state is authoritative" in captured["prompt"]
    assert "pending" in captured["prompt"]
    assert captured["persist_input_as_user"] is False
    assert captured["session_trigger"] == {"workspace": "modeling"}


def test_handoff_activity_is_bounded_and_preserves_latest_switch():
    trace = [{"tool_name": "sheet_metal.inspect", "result": {
        "ok": True, "objects": ["x" * 10000] * 100}} for _ in range(100)]
    last = {"tool_name": "workspace.switch", "result": {
        "ok": True, "workspace": "modeling", "next_turn_required": True}}
    trace.append(last)
    activity = session_module._native_surface_tool_activity(trace)
    assert len(json.dumps(activity).encode()) <= 24000
    assert activity["calls"][-1] == last
    assert activity["omitted_call_count"] > 0
    assert "handoff_result_omitted" in json.dumps(activity)
    assert len(trace) == 101
    assert len(trace[0]["result"]["objects"][0]) == 10000


def test_document_state_continuation_inspects_fresh_state_without_relabeling_a_workspace_change(monkeypatch):
    captured = {}
    def run_turn(prompt, **kwargs):
        captured.update(prompt=prompt, **kwargs)
        return SimpleNamespace(final_output="continued")
    monkeypatch.setattr(session_module, "_run_session_turn", run_turn)
    session_module.run_native_surface_continuation({
        "type": "cad_document_state_changed", "document_uid": "document-a",
        "document_name": "Design", "surface_id": "sheet_metal", "workspace": "sheet_metal"})
    assert captured["persist_input_as_user"] is False
    assert captured["prompt_section"] == "CURRENT_SESSION_EVENT"
    assert "Inspect the current document state" in captured["prompt"]
    assert "work is now available" not in captured["prompt"]
    assert captured["session_trigger"] == {"workspace": "sheet_metal"}


def test_native_surface_continuation_preserves_conversation_and_build_obligation(
    monkeypatch,
) -> None:
    captured = {}
    response = SimpleNamespace(final_output="continued")

    def run_turn(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return response

    monkeypatch.setattr(session_module, "_run_session_turn", run_turn)
    event = {
        "type": "cad_workspace_changed",
        "document_uid": "document-a",
        "document_name": "Design",
        "surface_id": "assemble",
        "workspace": "assembly",
    }

    result = session_module.run_native_surface_continuation(event)

    assert result is response
    assert captured["session_trigger"] == {"workspace": "assembly"}
    assert captured["persist_input_as_user"] is False
    assert captured["prompt_section"] == "CURRENT_SESSION_EVENT"
    assert "interaction_mode" not in captured
    assert captured["prompt"] == (
        "Assembly work is now available. Continue the current design from its "
        "existing document state. Do not repeat completed operations."
    )


def test_native_edit_continuation_accepts_exact_opened_sketch(monkeypatch) -> None:
    captured = {}

    def run_turn(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return SimpleNamespace(final_output="continued")

    monkeypatch.setattr(session_module, "_run_session_turn", run_turn)
    event = {
        "type": "cad_edit_started",
        "document_uid": "document-a",
        "document_name": "Design",
        "surface_id": "sketch.edit",
        "workspace": "sketching",
        "edit_object_name": "Sketch",
    }

    session_module.run_native_surface_continuation(event)

    assert captured["session_trigger"] == {"workspace": "sketching"}
    assert captured["persist_input_as_user"] is False
    assert "interaction_mode" not in captured


def test_native_provider_scope_continuation_resumes_without_a_user_message(
    monkeypatch,
) -> None:
    captured = {}

    def run_turn(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return SimpleNamespace(final_output="continued")

    monkeypatch.setattr(session_module, "_run_session_turn", run_turn)
    event = {
        "type": "cad_provider_surface_changed",
        "document_uid": "document-a",
        "document_name": "Design",
        "surface_id": "analyze",
        "workspace": "analysis",
    }

    session_module.run_native_surface_continuation(event)

    assert captured["session_trigger"] == {"workspace": "analysis"}
    assert captured["persist_input_as_user"] is False
    assert captured["prompt_section"] == "CURRENT_SESSION_EVENT"
    assert captured["prompt"] == (
        "Analysis tools now match the current study state. Continue the existing "
        "engineering task without repeating completed work."
    )
