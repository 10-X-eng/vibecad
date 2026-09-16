# SPDX-License-Identifier: LGPL-2.1-or-later

"""Conversation persistence must ignore detached document copies."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADGui as gui


def test_save_copy_does_not_relocate_active_project_artifacts(
    monkeypatch,
    tmp_path,
) -> None:
    live_file = tmp_path / "live.FCStd"
    copy_file = tmp_path / "detached" / "document.FCStd"
    document = SimpleNamespace(Uid="document-uid", FileName=str(live_file))
    calls = []

    class Service:
        def relocate_conversation_store_for_document_file(self, *args):
            calls.append(("conversation", args))

        def write_references_for_document_file(self, *args):
            calls.append(("references", args))

        def relocate_temporary_project_artifacts_for_document_file(self, *args):
            calls.append(("artifacts", args))

        def discard_temporary_project_root(self, *args):
            calls.append(("discard", args))

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    gui._document_save_conversations.clear()
    gui._document_save_references.clear()
    gui._document_save_conversations[document.Uid] = {
        "store_path": "/project/conversations",
        "temporary_project_root": "/project/unsaved-document",
    }
    gui._document_save_references[document.Uid] = {
        "references": [{"id": "reference"}],
    }

    gui._move_saved_document_conversation(document, str(copy_file))

    assert calls == []
    assert document.Uid not in gui._document_save_conversations
    assert document.Uid not in gui._document_save_references


@pytest.mark.parametrize("saved", [False, True])
def test_save_copy_does_not_persist_authoring_mode(monkeypatch, tmp_path, saved):
    document = SimpleNamespace(
        Uid="document-uid",
        FileName=str(tmp_path / "live.FCStd") if saved else "",
    )
    persisted = []
    warnings = []
    service = SimpleNamespace(
        persist_modeling_engine_after_save=lambda uid: persisted.append(uid),
    )
    monkeypatch.setattr(gui, "get_service", lambda: service)
    monkeypatch.setattr(gui, "_warn", warnings.append)
    monkeypatch.setattr(gui, "_schedule_assistant_document_refresh", lambda: None)
    observer = gui._VibeCADDocumentObserver()

    observer.slotFinishSaveDocument(document, str(tmp_path / "worker.FCStd"))
    assert persisted == []
    assert warnings == []

    # A subsequent real Save/Save As must still promote the session choice.
    document.FileName = str(tmp_path / "saved.FCStd")
    observer.slotFinishSaveDocument(document, document.FileName)
    assert persisted == [document.Uid]
    assert warnings == []


def test_real_save_still_reports_authoring_persistence_failure(monkeypatch, tmp_path):
    document = SimpleNamespace(Uid="document-uid", FileName=str(tmp_path / "live.FCStd"))
    warnings = []

    def fail(_uid):
        raise OSError("manifest write failed")

    monkeypatch.setattr(gui, "get_service", lambda: SimpleNamespace(
        persist_modeling_engine_after_save=fail,
    ))
    monkeypatch.setattr(gui, "_warn", warnings.append)
    monkeypatch.setattr(gui, "_schedule_assistant_document_refresh", lambda: None)

    gui._VibeCADDocumentObserver().slotFinishSaveDocument(document, document.FileName)

    assert warnings == ["VibeCAD authoring mode persistence failed: manifest write failed"]
