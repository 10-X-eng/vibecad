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
        self.Document = None

    def getTypeIdOfProperty(self, name: str) -> str:
        if (
            name == "VibeCADTimelineOwner"
            and hasattr(self, "VibeCADTimelineOwner")
        ):
            return "App::PropertyLinkHidden"
        return ""


class _ProjectionObject(_Object):
    def __init__(self, name: str) -> None:
        super().__init__(name, ["Touched"])
        self.PrecomputedProjectionSourceState = "projection-state"
        self.restore_calls = 0

    def restorePrecomputedState(self) -> bool:
        self.restore_calls += 1
        return True

    def purgeTouched(self) -> None:
        self.State = ["Up-to-date"]


class _Timeline:
    def __init__(
        self,
        operations: list[_Object],
        position: int,
        suppression: list[bool],
    ) -> None:
        self.Operations = list(operations)
        self.Position = position
        self.SuppressionAtEnd = list(suppression)


class _Document:
    def __init__(
        self,
        objects: list[_Object],
        timeline: _Timeline | None = None,
    ) -> None:
        self.Name = "RestoredDocument"
        self.Uid = "restored-document-uid"
        self.Restoring = False
        self.Recomputing = False
        self.Objects = list(objects)
        self.timeline = timeline
        self.recompute_calls = 0
        self.recursive_recompute_calls = 0
        self.on_recompute = None
        for obj in self.Objects:
            obj.Document = self

    def getObject(self, name: str):
        if name == "VibeCADTimeline":
            return self.timeline
        return next(
            (obj for obj in self.Objects if obj.Name == name),
            None,
        )

    def recompute(self, objects=None, *_args) -> int:
        if self.Recomputing:
            self.recursive_recompute_calls += 1
            return 0
        self.Recomputing = True
        self.recompute_calls += 1
        try:
            if self.on_recompute is not None:
                self.on_recompute()
            if hasattr(self, "_gui_document"):
                self._gui_document.Modified = True
            recomputed = 0
            candidates = self.Objects if objects is None else list(objects)
            for obj in candidates:
                if "Touched" not in obj.State:
                    continue
                obj.State = ["Up-to-date"]
                recomputed += 1
            return recomputed
        finally:
            self.Recomputing = False


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
    assert document.recursive_recompute_calls == 0
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


def test_restored_geometry_recompute_is_sliced_per_object(monkeypatch) -> None:
    first = _Object("FirstPendingFeature", ["Touched"])
    second = _Object("SecondPendingFeature", ["Touched"])
    document = _Document([first, second])
    _install_gui_document(monkeypatch, document)

    assert gui._recompute_pending_document_geometry_slice(document) == (
        True,
        True,
    )
    assert first.State == ["Up-to-date"]
    assert second.State == ["Touched"]

    assert gui._recompute_pending_document_geometry_slice(document) == (
        True,
        False,
    )
    assert second.State == ["Up-to-date"]
    assert document.recompute_calls == 2


def test_restored_projection_hydration_is_sliced_per_view() -> None:
    first = _ProjectionObject("FirstProjection")
    second = _ProjectionObject("SecondProjection")
    document = _Document([first, second])
    attempted: set[str] = set()

    assert gui._restore_precomputed_projection_slice(document, attempted) == (
        True,
        True,
    )
    assert first.restore_calls == 1
    assert second.restore_calls == 0

    assert gui._restore_precomputed_projection_slice(document, attempted) == (
        True,
        False,
    )
    assert second.restore_calls == 1
    assert attempted == {"FirstProjection", "SecondProjection"}


def test_non_3d_restored_view_flushes_gui_without_redraw_warning(
    monkeypatch,
) -> None:
    document = _Document([_Object("DrawingPage", ["Up-to-date"])])
    gui_updates = []
    warnings = []
    gui_document = SimpleNamespace(
        activeView=lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        gui.Gui,
        "getDocument",
        lambda name: gui_document if name == document.Name else None,
        raising=False,
    )
    monkeypatch.setattr(
        gui.Gui,
        "updateGui",
        lambda: gui_updates.append(True),
        raising=False,
    )
    monkeypatch.setattr(gui, "_warn", warnings.append)

    gui._redraw_document_view(document)

    assert gui_updates == [True]
    assert warnings == []


def test_pending_geometry_does_not_enter_an_active_recompute(monkeypatch) -> None:
    pending = _Object("PendingFeature", ["Touched"])
    document = _Document([pending])
    view = _install_gui_document(monkeypatch, document)
    document.Recomputing = True

    assert gui._recompute_pending_document_geometry(document) is False

    assert document.recompute_calls == 0
    assert document.recursive_recompute_calls == 0
    assert pending.State == ["Touched"]
    assert view.redraw_calls == 0


def test_future_and_suppressed_timeline_work_stays_deferred(
    monkeypatch,
) -> None:
    suppressed_owner = _Object("SuppressedJob", ["Up-to-date"])
    suppressed_resource = _Object("SuppressedStock", ["Touched"])
    suppressed_resource.VibeCADTimelineRole = "resource"
    suppressed_resource.VibeCADTimelineOwner = suppressed_owner
    future_owner = _Object("FutureJob", ["Touched"])
    future_resource = _Object("FutureStock", ["Touched"])
    future_resource.VibeCADTimelineRole = "resource"
    future_resource.VibeCADTimelineOwner = future_owner
    timeline = _Timeline(
        [suppressed_owner, future_owner],
        position=1,
        suppression=[True, False],
    )
    document = _Document(
        [
            suppressed_owner,
            suppressed_resource,
            future_owner,
            future_resource,
        ],
        timeline,
    )
    view = _install_gui_document(monkeypatch, document)
    warnings = []
    monkeypatch.setattr(gui, "_warn", warnings.append)

    assert gui._pending_document_objects(document) == []
    assert gui._recompute_pending_document_geometry(document) is False

    assert document.recompute_calls == 0
    assert suppressed_resource.State == ["Touched"]
    assert future_owner.State == ["Touched"]
    assert future_resource.State == ["Touched"]
    assert view.redraw_calls == 0
    assert warnings == []


def test_orphaned_and_malformed_timeline_resources_stay_deferred(
    monkeypatch,
) -> None:
    valid_owner = _Object("DeletedOwner", ["Up-to-date"])
    orphan = _Object("OrphanedResource", ["Touched"])
    orphan.VibeCADTimelineRole = "resource"
    orphan.VibeCADTimelineOwner = None

    malformed_owner = _Object("MalformedOwner", ["Up-to-date"])
    malformed = _Object("MalformedResource", ["Touched"])
    malformed.VibeCADTimelineRole = "resource"
    malformed.VibeCADTimelineOwner = malformed_owner
    malformed.getTypeIdOfProperty = (
        lambda _name: "App::PropertyLink"
    )

    timeline = _Timeline(
        [orphan, malformed_owner, malformed],
        position=3,
        suppression=[False, False, False],
    )
    document = _Document(
        [valid_owner, orphan, malformed_owner, malformed],
        timeline,
    )
    view = _install_gui_document(monkeypatch, document)
    warnings = []
    monkeypatch.setattr(gui, "_warn", warnings.append)

    assert gui._pending_document_objects(document) == []
    assert gui._recompute_pending_document_geometry(document) is False

    assert document.recompute_calls == 0
    assert orphan.State == ["Touched"]
    assert malformed.State == ["Touched"]
    assert view.redraw_calls == 0
    assert warnings == []


def test_future_timeline_error_is_reported_without_recompute(
    monkeypatch,
) -> None:
    future = _Object("BrokenFutureOperation", ["Touched", "Error"])
    document = _Document(
        [future],
        _Timeline([future], position=0, suppression=[False]),
    )
    view = _install_gui_document(monkeypatch, document)
    warnings = []
    monkeypatch.setattr(gui, "_warn", warnings.append)

    assert gui._recompute_pending_document_geometry(document) is False

    assert document.recompute_calls == 0
    assert future.State == ["Touched", "Error"]
    assert view.redraw_calls == 0
    assert len(warnings) == 1
    assert "BrokenFutureOperation (Error)" in warnings[0]


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
    assert document.Uid in gui._pending_document_render_refreshes
    assert callbacks[0][0] == 0

    callbacks.pop(0)[1]()

    assert view.redraw_calls == 2
    assert document._gui_updates == [True, True]
    assert document.Uid not in gui._pending_document_render_refreshes


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
    assert document.Uid in gui._pending_document_render_refreshes
    assert callbacks[0][0] == 0

    callbacks.pop(0)[1]()
    assert view.redraw_calls == 2
    assert document.Uid not in gui._pending_document_render_refreshes


def test_open_scheduler_rechecks_recompute_after_presentation(monkeypatch) -> None:
    document = _Document([_Object("PendingFeature", ["Touched"])])
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
        doc.Recomputing = True
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
    assert document.Uid in gui._pending_document_render_refreshes
    assert callbacks[0][0] == 100

    document.Recomputing = False
    callbacks.pop(0)[1]()

    assert restored == [document]
    assert document.recompute_calls == 1
    assert document.recursive_recompute_calls == 0
    assert view.redraw_calls == 1
    assert document.Uid in gui._pending_document_render_refreshes
    assert callbacks[0][0] == 0

    callbacks.pop(0)[1]()
    assert view.redraw_calls == 2
    assert document.Uid not in gui._pending_document_render_refreshes


def test_scheduler_owns_pending_key_during_its_recompute(monkeypatch) -> None:
    document = _Document([_Object("PendingFeature", ["Touched"])])
    view = _install_gui_document(monkeypatch, document)
    callbacks: list[tuple[int, object]] = []
    nested_callbacks = []
    warnings = []

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
    monkeypatch.setattr(
        gui,
        "_restore_partdesign_history_rendering",
        lambda doc: False,
    )
    monkeypatch.setattr(gui, "_warn", warnings.append)
    gui._pending_document_render_refreshes.discard(document.Uid)

    def reschedule_from_recompute_observer() -> None:
        callback_count = len(callbacks)
        gui._schedule_document_render_after_restore(document)
        # Native recompute progress processing can dispatch a zero-delay timer
        # before the owning recompute returns. Execute any callback that an
        # incorrect reschedule admitted to model that exact re-entrant path.
        while len(callbacks) > callback_count:
            delay, callback = callbacks.pop(callback_count)
            nested_callbacks.append(delay)
            callback()

    document.on_recompute = reschedule_from_recompute_observer

    gui._schedule_document_render_after_restore(document)
    callbacks.pop(0)[1]()

    assert document.recompute_calls == 1
    assert document.recursive_recompute_calls == 0
    assert nested_callbacks == []
    assert warnings == []
    assert view.redraw_calls == 1
    assert document.Uid in gui._pending_document_render_refreshes
    assert len(callbacks) == 1
    assert callbacks[0][0] == 0

    callbacks.pop(0)[1]()
    assert view.redraw_calls == 2
    assert document.Uid not in gui._pending_document_render_refreshes


def test_open_scheduler_drops_a_document_closed_before_its_callback(
    monkeypatch,
) -> None:
    document = _Document([_Object("PendingFeature", ["Touched"])])
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
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    monkeypatch.setattr(gui.App, "listDocuments", lambda: {}, raising=False)
    gui._pending_document_render_refreshes.discard(document.Uid)

    gui._schedule_document_render_after_restore(document)
    assert document.Uid in gui._pending_document_render_refreshes

    # Closing a native document invalidates its Python wrapper before queued
    # zero-delay callbacks run. The callback must resolve by stable identity,
    # notice that no live document remains, and stop without touching it.
    callbacks.pop(0)[1]()

    assert document.recompute_calls == 0
    assert document.Uid not in gui._pending_document_render_refreshes
    assert callbacks == []


def test_open_scheduler_drops_final_redraw_after_document_closes(
    monkeypatch,
) -> None:
    document = _Document([_Object("PendingFeature", ["Touched"])])
    _install_gui_document(monkeypatch, document)
    callbacks: list[tuple[int, object]] = []
    live_documents = {document.Name: document}
    warnings = []

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
        lambda: dict(live_documents),
        raising=False,
    )
    monkeypatch.setattr(gui, "_warn", warnings.append)
    gui._pending_document_render_refreshes.discard(document.Uid)

    gui._schedule_document_render_after_restore(document)
    callbacks.pop(0)[1]()

    assert document.recompute_calls == 1
    assert document.Uid in gui._pending_document_render_refreshes
    assert callbacks[0][0] == 0

    # The first restore callback can finish and schedule a final redraw just
    # before the user closes the document. Resolve the document again instead
    # of retaining its invalidated Python wrapper.
    live_documents.clear()
    callbacks.pop(0)[1]()

    assert callbacks == []
    assert warnings == []
    assert document.Uid not in gui._pending_document_render_refreshes


def test_document_observer_schedules_new_documents_for_render(monkeypatch) -> None:
    document = _Document([])
    scheduled = []
    initialized = []
    monkeypatch.setattr(
        gui,
        "get_service",
        lambda: SimpleNamespace(
            ensure_native_document_state=initialized.append,
        ),
    )
    monkeypatch.setattr(
        gui,
        "_schedule_document_render_after_restore",
        lambda doc: scheduled.append(doc),
    )
    monkeypatch.setattr(gui, "_schedule_assistant_document_refresh", lambda: None)

    gui._VibeCADDocumentObserver().slotCreatedDocument(document)

    assert scheduled == [document]
    assert initialized == [document.Uid]
