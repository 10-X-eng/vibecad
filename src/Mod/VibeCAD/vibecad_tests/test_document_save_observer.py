# SPDX-License-Identifier: LGPL-2.1-or-later

"""Conversation persistence must ignore detached document copies."""

from __future__ import annotations

from types import SimpleNamespace

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
