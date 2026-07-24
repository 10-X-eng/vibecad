# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression coverage for render-ready documents immediately after open."""

from __future__ import annotations

from types import SimpleNamespace
import sys

import VibeCADGui as gui


class _Object:
    def __init__(self, name: str, state: list[str]) -> None:
        self.Name = name
        self.State = list(state)


class _Document:
    def __init__(self, objects: list[_Object]) -> None:
        self.Name = "RestoredDocument"
        self.Uid = "restored-document-uid"
        self.Restoring = False
        self.Objects = list(objects)
        self.recompute_calls = 0

    def recompute(self) -> int:
        self.recompute_calls += 1
        if hasattr(self, "_gui_document"):
            self._gui_document.Modified = True
        recomputed = 0
        for obj in self.Objects:
            if "Touched" not in obj.State:
                continue
            obj.State = ["Up-to-date"]
            recomputed += 1
        return recomputed


class _View:
    def __init__(self) -> None:
        self.redraw_calls = 0

    def redraw(self) -> None:
        self.redraw_calls += 1


def _install_gui_document(monkeypatch, document: _Document) -> _View:
    view = _View()
    gui_document = SimpleNamespace(Modified=False, activeView=lambda: view)
    document._gui_document = gui_document
    document._gui_updates = []
    monkeypatch.setattr(
        gui.Gui,
        "getDocument",
        lambda name: gui_document if name == document.Name else None,
        raising=False,
    )
    monkeypatch.setattr(
        gui.Gui,
        "updateGui",
        lambda: document._gui_updates.append(True),
        raising=False,
    )
    return view


def test_restored_pending_geometry_is_recomputed_and_redrawn(monkeypatch) -> None:
    pending = _Object("PendingFeature", ["Touched"])
    clean = _Object("CleanFeature", ["Up-to-date"])
    document = _Document([pending, clean])
    view = _install_gui_document(monkeypatch, document)

    assert gui._recompute_pending_document_geometry(document) is True

    assert document.recompute_calls == 1
    assert pending.State == ["Up-to-date"]
    assert clean.State == ["Up-to-date"]
    assert view.redraw_calls == 1
    assert document._gui_updates == [True]
    assert document._gui_document.Modified is False


def test_open_recompute_preserves_an_existing_modified_state(monkeypatch) -> None:
    document = _Document([_Object("PendingFeature", ["Touched"])])
    _install_gui_document(monkeypatch, document)
    document._gui_document.Modified = True

    assert gui._recompute_pending_document_geometry(document) is True

    assert document._gui_document.Modified is True


def test_clean_restored_document_is_not_recomputed(monkeypatch) -> None:
    document = _Document([_Object("CleanFeature", ["Up-to-date"])])
    view = _install_gui_document(monkeypatch, document)

    assert gui._recompute_pending_document_geometry(document) is False

    assert document.recompute_calls == 0
    assert view.redraw_calls == 0


def test_open_scheduler_waits_for_restore_then_makes_document_render_ready(
    monkeypatch,
) -> None:
    document = _Document([_Object("PendingFeature", ["Touched"])])
    view = _install_gui_document(monkeypatch, document)
    restoring = [True]
    callbacks: list[tuple[int, object]] = []

    class _Timer:
        @staticmethod
        def singleShot(delay: int, callback) -> None:
            callbacks.append((delay, callback))

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtCore=SimpleNamespace(QTimer=_Timer)),
    )
    monkeypatch.setattr(
        gui.App,
        "isRestoring",
        lambda: restoring[0],
        raising=False,
    )
    monkeypatch.setattr(
        gui.App,
        "listDocuments",
        lambda: {document.Name: document},
        raising=False,
    )
    gui._pending_document_render_refreshes.discard(document.Uid)

    gui._schedule_document_render_after_restore(document)
    assert callbacks[0][0] == 0

    callbacks.pop(0)[1]()
    assert document.recompute_calls == 0
    assert callbacks[0][0] == 100

    restoring[0] = False
    callbacks.pop(0)[1]()

    assert document.recompute_calls == 1
    assert document.Objects[0].State == ["Up-to-date"]
    assert view.redraw_calls == 1
    assert document.Uid not in gui._pending_document_render_refreshes
    assert callbacks[0][0] == 0

    callbacks.pop(0)[1]()

    assert view.redraw_calls == 2
    assert document._gui_updates == [True, True]


def test_open_scheduler_redraws_restored_partdesign_history(monkeypatch) -> None:
    document = _Document([_Object("CleanFeature", ["Up-to-date"])])
    view = _install_gui_document(monkeypatch, document)
    callbacks: list[tuple[int, object]] = []
    restored = []

    class _Timer:
        @staticmethod
        def singleShot(delay: int, callback) -> None:
            callbacks.append((delay, callback))

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtCore=SimpleNamespace(QTimer=_Timer)),
    )
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    monkeypatch.setattr(
        gui.App,
        "listDocuments",
        lambda: {document.Name: document},
        raising=False,
    )

    def restore_history(doc) -> bool:
        restored.append(doc)
        return True

    monkeypatch.setattr(
        gui,
        "_restore_partdesign_history_rendering",
        restore_history,
    )
    gui._pending_document_render_refreshes.discard(document.Uid)

    gui._schedule_document_render_after_restore(document)
    callbacks.pop(0)[1]()

    assert restored == [document]
    assert document.recompute_calls == 0
    assert view.redraw_calls == 1
    assert document._gui_document.Modified is False
    assert callbacks[0][0] == 0

    callbacks.pop(0)[1]()
    assert view.redraw_calls == 2


def test_document_observer_schedules_new_documents_for_render(monkeypatch) -> None:
    document = _Document([])
    scheduled = []
    monkeypatch.setattr(
        gui,
        "_schedule_document_render_after_restore",
        lambda doc: scheduled.append(doc),
    )
    monkeypatch.setattr(gui, "_schedule_assistant_document_refresh", lambda: None)

    gui._VibeCADDocumentObserver().slotCreatedDocument(document)

    assert scheduled == [document]
