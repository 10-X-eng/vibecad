# SPDX-License-Identifier: MIT
"""Pending presets must stay idle and retain their exact document/object owner."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def pending(monkeypatch):
    documents = {}
    app = SimpleNamespace(ActiveDocument=None, getDocument=documents.get)
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    spec = importlib.util.spec_from_file_location(
        "scs_pending_test", Path(__file__).resolve().parents[1] / "pending_unfold.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_param", lambda: None)
    scheduled = []
    monkeypatch.setattr(module, "_schedule", lambda fn, delay: scheduled.append(fn))
    monkeypatch.setattr(module, "_post_apply", scheduled.append, raising=False)
    calls = []
    monkeypatch.setitem(sys.modules, "bend_actions", SimpleNamespace(
        sync_unfold_features=lambda *args, **kwargs: calls.append((args, kwargs)) or 1
    ))
    monkeypatch.setitem(sys.modules, "preset_update", SimpleNamespace(
        run_document_update=lambda document, operation: operation()
    ))
    owner = SimpleNamespace(Name="Owner", Uid="owner-uid")
    objects = {}
    owner.getObject = objects.get
    documents[owner.Name] = owner
    obj = SimpleNamespace(Name="Unfold", Label="Unfold", Document=owner,
                          KFactor=0.4, MaterialSheet="")
    objects[obj.Name] = obj
    return SimpleNamespace(module=module, app=app, documents=documents, owner=owner,
                           obj=obj, objects=objects, scheduled=scheduled, calls=calls)


def activate(pending):
    pending.module._pending = {"doc": "Owner", "k": 0.5, "sheet": "material_SCS"}


def test_no_pending_preset_schedules_no_callbacks(pending):
    observer = pending.module._UnfoldPendingObserver()
    for i in range(100):
        observer.slotCreatedObject(SimpleNamespace(Name=f"Box{i}", Document=pending.owner))
    assert pending.scheduled == []


def test_pending_preset_ignores_ordinary_objects(pending):
    activate(pending)
    pending.module._defer_apply(SimpleNamespace(Name="Box", Label="Box", Document=pending.owner))
    assert pending.scheduled == []


def test_pending_preset_never_applies_to_another_document(pending):
    activate(pending)
    pending.obj.Document = SimpleNamespace(Name="Other", Uid="other-uid")
    assert not pending.module.apply_pending_to_object(pending.obj)
    assert pending.calls == []


def test_pending_apply_targets_its_object_and_owner_when_active_document_changes(pending):
    activate(pending)
    pending.app.ActiveDocument = SimpleNamespace(Name="Other", Uid="other-uid")
    assert pending.module.apply_pending_to_object(pending.obj)
    args, kwargs = pending.calls[0]
    assert kwargs["document"] is pending.owner
    assert kwargs["objects"] == [pending.obj]
    assert kwargs["recompute"] is False


def test_duplicate_notifications_share_one_queued_apply(pending):
    activate(pending)
    observer = pending.module._UnfoldPendingObserver()
    observer.slotCreatedObject(pending.obj)
    observer.slotAppendObject(None, pending.obj)
    assert len(pending.scheduled) == 1
    pending.scheduled.pop()()
    assert len(pending.calls) == 1


def test_deferred_apply_does_not_target_replacement_object_with_reused_name(pending):
    activate(pending)
    pending.module._defer_apply(pending.obj)
    pending.objects[pending.obj.Name] = SimpleNamespace(
        Name="Unfold", Label="Unfold", Document=pending.owner, KFactor=0.4, MaterialSheet=""
    )
    pending.scheduled.pop()()
    assert pending.calls == []


def test_scoped_sync_updates_only_the_requested_object_in_its_owner(pending, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace(
        Selection=SimpleNamespace(getSelection=lambda: [])
    ))
    spec = importlib.util.spec_from_file_location("scs_actions_test", root / "bend_actions.py")
    actions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actions)
    recomputes = []
    pending.owner.recompute = lambda: recomputes.append(pending.owner)
    other = SimpleNamespace(Name="Unfold001", Label="Unfold001", Document=pending.owner,
                            KFactor=0.4, MaterialSheet="")
    pending.objects[other.Name] = other
    pending.owner.Objects = [pending.obj, other]
    pending.app.ActiveDocument = SimpleNamespace(Name="Other", getObject=lambda name: None)
    assert actions.sync_unfold_features(
        "material_SCS", 0.5, document=pending.owner, objects=[pending.obj]
    ) == 1
    assert pending.obj.KFactor == 0.5
    assert other.KFactor == 0.4
    assert recomputes == [pending.owner]


def test_startup_without_pending_preset_does_not_install_observer(pending):
    installed = []
    pending.app.addDocumentObserver = installed.append
    pending.module.setup()
    assert installed == []


def test_clearing_pending_preset_disconnects_observer(pending):
    installed, removed = [], []
    pending.app.addDocumentObserver = installed.append
    pending.app.removeDocumentObserver = removed.append
    pending.module.remember_pending_unfold_sync(0.5, "material_SCS", doc_name="Owner")
    assert len(installed) == 1
    pending.module.clear_pending_unfold_sync()
    assert removed == installed
    assert pending.module._observer is None


def test_pending_preset_keeps_document_uid_across_same_name_reuse(pending):
    pending.app.addDocumentObserver = lambda observer: None
    pending.module.remember_pending_unfold_sync(0.5, "material_SCS", doc_name="Owner")
    assert pending.module.get_pending()["doc_uid"] == "owner-uid"
    pending.owner.Uid = "replacement-document-uid"
    assert not pending.module.apply_pending_to_object(pending.obj)
    pending.module._defer_apply(pending.obj)
    assert pending.calls == []
    assert pending.scheduled == []


def test_clear_pending_still_clears_state_if_observer_was_already_removed(pending):
    activate(pending)
    pending.module._observer = object()
    def already_removed(observer):
        raise RuntimeError("observer already removed")
    pending.app.removeDocumentObserver = already_removed
    pending.module.clear_pending_unfold_sync()
    assert pending.module.get_pending() is None
    assert pending.module._observer is None


def test_busy_pending_apply_waits_for_recompute_event_without_polling(pending):
    activate(pending)
    pending.owner.RecomputePending = True
    pending.module._defer_apply(pending.obj)
    pending.scheduled.pop()()
    assert pending.calls == []
    assert pending.scheduled == []
    pending.owner.RecomputePending = False
    pending.module._UnfoldPendingObserver().slotRecomputedDocument(pending.owner)
    assert len(pending.scheduled) == 1
    pending.scheduled.pop()()
    assert len(pending.calls) == 1


def test_pending_apply_resumes_when_immediate_mutation_finishes(pending):
    activate(pending)
    pending.owner.CooperativeMutationActive = True
    pending.module._defer_apply(pending.obj)
    pending.scheduled.pop()()
    observer = pending.module._UnfoldPendingObserver()
    observer.slotCooperativeMutationChanged(pending.owner, True)
    assert pending.scheduled == []
    pending.owner.CooperativeMutationActive = False
    observer.slotCooperativeMutationChanged(pending.owner, False)
    assert len(pending.scheduled) == 1
    pending.scheduled.pop()()
    assert len(pending.calls) == 1


def test_undo_clears_the_owning_documents_pending_preset(pending):
    activate(pending)
    pending.module._UnfoldPendingObserver().slotUndoDocument(pending.owner)
    assert pending.module.get_pending() is None


def test_undo_in_another_document_preserves_pending_preset(pending):
    activate(pending)
    pending.module._UnfoldPendingObserver().slotUndoDocument(
        SimpleNamespace(Name="Other", Uid="other-uid")
    )
    assert pending.module.get_pending()["k"] == 0.5
