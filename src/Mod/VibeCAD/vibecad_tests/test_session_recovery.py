# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for crash-safe VibeCAD composer and run recovery."""

from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace

from VibeCADCore import VibeCADService
import VibeCADGui as gui
from VibeCADSessionRecovery import (
    RECOVERY_FILE_NAME,
    SessionRecoveryStore,
    recovery_composer_text,
)


def _snapshot(*, phase: str = "draft", prompt: str = "Make a 20 mm box.") -> dict:
    return {
        "phase": phase,
        "prompt": prompt,
        "document_uid": "document-uid",
        "document_name": "Bracket",
        "file_path": "/projects/bracket.FCStd",
        "conversation_id": "a" * 32,
    }


def test_recovery_store_atomically_round_trips_a_draft(tmp_path) -> None:
    store = SessionRecoveryStore(tmp_path)
    snapshot = _snapshot()
    snapshot["instance_id"] = "previous-vibecad-instance"

    written = store.write(snapshot)
    loaded = store.load(
        document_uid="document-uid",
        conversation_id="a" * 32,
    )

    assert written["written"] is True
    assert written["path"] == str(tmp_path / RECOVERY_FILE_NAME)
    assert loaded["available"] is True
    assert loaded["recoverable"] is True
    assert loaded["phase"] == "draft"
    assert loaded["prompt"] == "Make a 20 mm box."
    assert loaded["instance_id"] == "previous-vibecad-instance"
    assert not (tmp_path / f"{RECOVERY_FILE_NAME}.tmp").exists()


def test_empty_draft_discards_recovery_but_running_prompt_is_kept(tmp_path) -> None:
    store = SessionRecoveryStore(tmp_path)
    store.write(_snapshot())

    discarded = store.write(_snapshot(prompt="   "))

    assert discarded == {
        "written": False,
        "discarded": True,
        "path": str(tmp_path / RECOVERY_FILE_NAME),
    }
    assert store.load(document_uid="document-uid")["available"] is False

    store.write(_snapshot(phase="running", prompt=""))
    loaded = store.load(document_uid="document-uid")
    assert loaded["available"] is True
    assert loaded["phase"] == "running"
    assert recovery_composer_text(loaded) == (
        "Continue the interrupted CAD work. Inspect the current document state "
        "before making any changes."
    )


def test_recovery_store_rejects_another_document_or_conversation(tmp_path) -> None:
    store = SessionRecoveryStore(tmp_path)
    store.write(_snapshot(phase="running"))

    other_document = store.load(document_uid="another-document")
    other_conversation = store.load(
        document_uid="document-uid",
        conversation_id="b" * 32,
    )

    assert other_document == {
        "available": False,
        "reason": "document_changed",
        "path": str(tmp_path / RECOVERY_FILE_NAME),
    }
    assert other_conversation == {
        "available": False,
        "reason": "conversation_changed",
        "path": str(tmp_path / RECOVERY_FILE_NAME),
    }


def test_corrupt_recovery_is_reported_and_can_be_discarded(tmp_path) -> None:
    path = tmp_path / RECOVERY_FILE_NAME
    path.write_text(json.dumps({"schema": "wrong", "prompt": "unsafe"}))
    store = SessionRecoveryStore(tmp_path)

    loaded = store.load(document_uid="document-uid")

    assert loaded["available"] is True
    assert loaded["recoverable"] is False
    assert loaded["path"] == str(path)
    assert "schema" in loaded["error"].lower()
    assert store.discard()["discarded"] is True
    assert not path.exists()


def test_running_recovery_restores_prompt_for_review_without_auto_replay(
    tmp_path,
) -> None:
    store = SessionRecoveryStore(tmp_path)
    store.write(_snapshot(phase="running", prompt="Add two mounting holes."))

    loaded = store.load(
        document_uid="document-uid",
        conversation_id="a" * 32,
    )

    assert recovery_composer_text(loaded) == "Add two mounting holes."
    assert loaded["phase"] == "running"
    assert "auto_resume" not in loaded


def test_service_captures_document_identity_before_off_thread_recovery_write(
    tmp_path,
) -> None:
    service = object.__new__(VibeCADService)
    service.project_scope_snapshot = lambda: {
        "root": str(tmp_path),
        "document": {
            "uid": "document-uid",
            "document": "Bracket",
            "file_path": "/projects/bracket.FCStd",
        },
    }
    service._active_document_uid = lambda: "document-uid"
    service._conversation_cache_key = str(
        tmp_path / "conversations" / f"{'a' * 32}.json"
    )

    prepared = service.prepare_session_recovery(
        "running",
        "Add two mounting holes.",
    )
    written = service.persist_prepared_session_recovery(prepared)
    loaded = service.session_recovery()

    assert prepared == {
        "project_root": str(tmp_path),
        "phase": "running",
        "prompt": "Add two mounting holes.",
        "document_uid": "document-uid",
        "document_name": "Bracket",
        "file_path": "/projects/bracket.FCStd",
        "conversation_id": "a" * 32,
    }
    assert written["written"] is True
    assert loaded["recoverable"] is True
    assert loaded["prompt"] == "Add two mounting holes."
    assert service.discard_session_recovery()["discarded"] is True


def test_recovery_banner_explains_that_interrupted_tools_are_not_replayed() -> None:
    text = gui._session_recovery_banner_text(
        {
            "available": True,
            "recoverable": True,
            "phase": "running",
            "prompt": "Add two mounting holes.",
        }
    )

    assert "interrupted" in text.lower()
    assert "not replay" in text.lower()
    assert "review" in text.lower()


def test_current_process_snapshot_is_not_presented_as_a_crash() -> None:
    assert gui._session_recovery_is_current_instance(
        {"instance_id": gui._session_recovery_instance_id}
    ) is True
    assert gui._session_recovery_is_current_instance(
        {"instance_id": "previous-vibecad-instance"}
    ) is False


def test_restore_moves_interrupted_prompt_to_composer_for_review(
    monkeypatch,
) -> None:
    recovered = {
        "available": True,
        "recoverable": True,
        "phase": "running",
        "prompt": "Add two mounting holes.",
    }

    class _Prompt:
        def __init__(self) -> None:
            self.value = ""
            self.focused = False

        def setPlainText(self, value: str) -> None:
            self.value = value

        def setFocus(self) -> None:
            self.focused = True

    class _Banner:
        def __init__(self) -> None:
            self.visible = True

        def setVisible(self, value: bool) -> None:
            self.visible = value

    class _Service:
        @staticmethod
        def session_recovery() -> dict:
            return dict(recovered)

    prompt = _Prompt()
    banner = _Banner()
    queued: list[tuple[str, str]] = []
    widgets = {
        ("QPlainTextEdit", "VibePrompt"): prompt,
        ("QFrame", "VibeSessionRecoveryBanner"): banner,
    }
    monkeypatch.setattr(gui, "get_service", lambda: _Service())
    monkeypatch.setattr(
        gui,
        "_find_child",
        lambda widget_type, name, _dock=None: widgets.get((widget_type, name)),
    )
    monkeypatch.setattr(
        gui,
        "_queue_session_recovery",
        lambda phase, text: queued.append((phase, text)),
    )

    gui._restore_session_recovery_from_panel(object())

    assert prompt.value == "Add two mounting holes."
    assert prompt.focused is True
    assert banner.visible is False
    assert queued == [("draft", "Add two mounting holes.")]


def test_programmatic_prompt_clear_cancels_pending_draft_autosave() -> None:
    calls: list[object] = []

    class _Timer:
        @staticmethod
        def stop() -> None:
            calls.append("stop")

    class _Prompt:
        _vibecad_session_recovery_timer = _Timer()

        @staticmethod
        def blockSignals(value: bool) -> bool:
            calls.append(("block", value))
            return False

        @staticmethod
        def clear() -> None:
            calls.append("clear")

    gui._clear_prompt_without_recovery(_Prompt())

    assert calls == ["stop", ("block", True), "clear", ("block", False)]


def test_unclaimed_recovery_is_not_overwritten_by_composer_autosave(
    monkeypatch,
) -> None:
    class _Banner:
        @staticmethod
        def isVisible() -> bool:
            return True

    class _Prompt:
        @staticmethod
        def toPlainText() -> str:
            return "A newer draft that has not claimed the recovery."

    dock = object()
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(gui, "_is_assistant_run_active", lambda: False)
    monkeypatch.setattr(gui, "_assistant_document_state", lambda: {"enabled": True})
    monkeypatch.setattr(gui, "_find_dock", lambda: dock)
    monkeypatch.setattr(
        gui,
        "_find_child",
        lambda widget_type, name, _dock=None: (
            _Banner()
            if (widget_type, name) == ("QFrame", "VibeSessionRecoveryBanner")
            else None
        ),
    )
    monkeypatch.setattr(
        gui,
        "_queue_session_recovery",
        lambda phase, text: queued.append((phase, text)),
    )

    gui._persist_prompt_recovery(_Prompt())

    assert queued == []


def test_shutdown_waits_for_queued_recovery_before_final_snapshot(
    monkeypatch,
) -> None:
    calls: list[object] = []

    class _Queue:
        @staticmethod
        def join() -> None:
            calls.append("join")

    class _Banner:
        @staticmethod
        def isVisible() -> bool:
            return False

    class _Prompt:
        @staticmethod
        def toPlainText() -> str:
            return "Keep this exact draft."

    class _Service:
        @staticmethod
        def prepare_session_recovery(phase, prompt, *, instance_id):
            calls.append(("prepare", phase, prompt, instance_id))
            return {"prepared": True}

        @staticmethod
        def persist_prepared_session_recovery(prepared) -> None:
            calls.append(("persist", prepared))

    dock = object()
    widgets = {
        ("QFrame", "VibeSessionRecoveryBanner"): _Banner(),
        ("QPlainTextEdit", "VibePrompt"): _Prompt(),
    }
    monkeypatch.setattr(gui, "_session_recovery_persist_queue", _Queue())
    monkeypatch.setattr(gui, "_find_dock", lambda: dock)
    monkeypatch.setattr(gui, "_assistant_panel_is_built", lambda value: value is dock)
    monkeypatch.setattr(
        gui,
        "_find_child",
        lambda widget_type, name, _dock=None: widgets.get((widget_type, name)),
    )
    monkeypatch.setattr(gui, "get_service", lambda: _Service())
    monkeypatch.setattr(gui, "_active_session_recovery", None)

    gui._persist_session_recovery_before_shutdown()

    assert calls[0] == "join"
    assert calls[1] == (
        "prepare",
        "draft",
        "Keep this exact draft.",
        gui._session_recovery_instance_id,
    )
    assert calls[2] == ("persist", {"prepared": True})


def test_recovery_writer_failure_does_not_wait_on_the_document_thread(
    monkeypatch,
) -> None:
    class _StopLoop(BaseException):
        pass

    class _Queue:
        @staticmethod
        def get():
            return _Service(), {"prepared": True}

        @staticmethod
        def task_done() -> None:
            raise _StopLoop

    class _Service:
        @staticmethod
        def persist_prepared_session_recovery(_prepared) -> None:
            raise OSError("recovery disk unavailable")

    dispatches: list[object] = []
    warnings: list[str] = []
    monkeypatch.setattr(gui, "_session_recovery_persist_queue", _Queue())
    monkeypatch.setattr(
        gui,
        "_dispatch_to_document_thread",
        lambda operation: dispatches.append(operation),
    )
    monkeypatch.setattr(gui, "_warn", warnings.append)

    try:
        gui._session_recovery_persist_loop()
    except _StopLoop:
        pass

    assert dispatches == []
    assert warnings == [
        "VibeCAD session recovery save failed: recovery disk unavailable"
    ]


def test_assistant_run_marks_running_recovery_then_clears_it(
    monkeypatch,
) -> None:
    queued: list[tuple[str, str]] = []

    class _Service:
        @staticmethod
        def use_online_provider_by_default() -> bool:
            return True

    response = SimpleNamespace(
        final_output="Done.",
        error=None,
        context={},
        tool_trace=(),
    )
    monkeypatch.setattr(gui, "_internal_agent_allowed", lambda: True)
    monkeypatch.setattr(gui, "_is_intent_memory_rebuild_active", lambda: False)
    monkeypatch.setattr(gui, "_ensure_document_thread_invoker", lambda: None)
    monkeypatch.setattr(gui, "_dispatch_to_document_thread", lambda operation: operation())
    monkeypatch.setattr(gui, "_render_assistant_run_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_clear_thinking", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_cancel_question_round", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_arm_sketch_close_continuation", lambda: None)
    monkeypatch.setattr(gui, "_refresh_conversation_selector", lambda *_args: None)
    monkeypatch.setattr(gui, "_refresh_view_status", lambda *_args: None)
    monkeypatch.setattr(gui, "_render_questions", lambda *_args: None)
    monkeypatch.setattr(gui, "_append_conversation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gui, "_find_dock", lambda: None)
    monkeypatch.setattr(gui, "run_prompt", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(
        gui,
        "_queue_session_recovery",
        lambda phase, text: queued.append((phase, text)),
    )
    monkeypatch.setattr(gui.App, "ActiveDocument", None, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "VibeCADScriptedEditor",
        SimpleNamespace(refresh_scripted_model_editor=lambda: None),
    )
    gui._assistant_run_thread = None
    gui._active_session_recovery = None

    gui._execute_assistant_run(
        object(),
        _Service(),
        prompt="Add two mounting holes.",
    )
    deadline = time.monotonic() + 2.0
    while gui._assistant_run_controller.snapshot()["active"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert queued == [
        ("running", "Add two mounting holes."),
        ("draft", ""),
    ]
    assert gui._active_session_recovery is None
