# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import VibeCADSession as session_module


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
