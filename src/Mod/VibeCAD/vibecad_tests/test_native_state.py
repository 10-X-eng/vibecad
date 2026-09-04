# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeState as state_module
from VibeCADNativeState import (
    NATIVE_AUTHORITY_CONFLICT,
    NATIVE_REVISION_CONFLICT,
    NativeAuthorityConflict,
    NativeDocumentStateStore,
    NativeObjectIdentity,
    NativeRevisionConflict,
    NativeStateError,
    is_structural_property,
)


@pytest.mark.parametrize(
    "property_name",
    (
        "Visibility",
        "VisibilityAtEnd",
        "LineColor",
        "SelectionStyle",
        "Touched",
        "_LinkTouched",
        "_GroupTouched",
        "PrecomputedDimensionFlags",
        "PrecomputedDimensionScalars",
        "PrecomputedDimensionVectors",
        "PrecomputedEdgeClasses",
        "PrecomputedEdgeVisibility",
        "PrecomputedProjectionCentroid",
        "PrecomputedProjectionEdges",
        "PrecomputedProjectionFaces",
        "PrecomputedProjectionSourceState",
        "PrecomputedSourceIndices",
        "VibeCADVibeScriptEditorDraft",
    ),
)
def test_presentation_and_transient_properties_are_not_structural(
    property_name: str,
) -> None:
    assert is_structural_property(property_name) is False


@pytest.mark.parametrize(
    "property_name",
    (
        "Shape",
        "Placement",
        "Geometry",
        "Constraints",
        "Group",
        "ExpressionEngine",
        "Label",
        "Status",
    ),
)
def test_model_properties_are_structural(property_name: str) -> None:
    assert is_structural_property(property_name) is True


def test_structural_revision_is_monotonic_and_presentation_is_ignored() -> None:
    store = NativeDocumentStateStore()

    assert store.ensure_document("document-a") == 0
    assert store.note_object_property_change("document-a", "Visibility") == 0
    assert store.note_structural_change("document-a") == 1
    assert store.note_object_property_change("document-a", "Placement") == 2
    assert store.current_revision("document-a") == 2


def test_techdraw_dimension_caches_do_not_advance_structural_revision() -> None:
    store = NativeDocumentStateStore()

    assert store.note_object_property_change("document-a", "SavedGeometry") == 0
    assert store.note_object_property_change("document-a", "BoxCorners") == 0
    assert store.note_object_property_change("document-a", "References2D") == 1


def test_manual_visibility_change_does_not_lock_native_authority() -> None:
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")

    store.note_object_property_change("document-a", "Visibility")

    store.require_vibescript_return_safe("document-a")


def test_manual_geometry_change_locks_native_authority() -> None:
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")

    store.note_object_property_change("document-a", "Shape")

    with pytest.raises(NativeAuthorityConflict):
        store.require_vibescript_return_safe("document-a")


def test_scoped_analyze_authority_does_not_take_geometry_authority() -> None:
    store = NativeDocumentStateStore()
    scope = store.begin_scoped_authority("document-a", "analyze")

    analyze_ticket = store.begin_call("document-a", "analyze.model")
    assert store.authorize_mutation(analyze_ticket).duplicate is False
    store.note_structural_change("document-a")
    store.complete_mutation(analyze_ticket, {"analysis": "Analysis"})

    model_ticket = store.begin_call("document-a", "model.feature")
    with pytest.raises(NativeStateError, match="not active"):
        store.authorize_mutation(model_ticket)

    store.end_scoped_authority("document-a", scope)
    store.require_vibescript_return_safe("document-a")
    assert store.snapshot("document-a")["native_authority"]["active"] is False
    assert store.snapshot("document-a")["recent_receipts"] == []


def test_scoped_drawing_authority_cannot_mutate_source_or_analysis_namespaces() -> None:
    store = NativeDocumentStateStore()
    scope = store.begin_scoped_authority(
        "document-a",
        "drawing",
        exact_capabilities=("document.undo",),
    )

    drawing_ticket = store.begin_call("document-a", "drawing.page")
    assert store.authorize_mutation(drawing_ticket).duplicate is False
    undo_ticket = store.begin_call("document-a", "document.undo")
    assert store.authorize_mutation(undo_ticket).duplicate is False

    for capability in ("model.feature", "analyze.model", "vibescript.create_program"):
        with pytest.raises(NativeStateError, match="not active"):
            store.authorize_mutation(store.begin_call("document-a", capability))

    store.end_scoped_authority("document-a", scope)
    store.cancel_mutation(undo_ticket)
    store.complete_mutation(drawing_ticket, {"page": "Page"})
    store.require_vibescript_return_safe("document-a")
    assert store.snapshot("document-a")["native_authority"]["active"] is False
    assert store.snapshot("document-a")["recent_receipts"] == []


def test_scoped_authority_is_exact_and_ends_outstanding_calls() -> None:
    store = NativeDocumentStateStore()
    scope = store.begin_scoped_authority("document-a", "analyze")
    ticket = store.begin_call("document-a", "analyze.load")
    store.authorize_mutation(ticket)

    store.end_scoped_authority("document-a", scope)

    store.complete_mutation(ticket, {"load": "Force"})
    assert store.snapshot("document-a")["recent_receipts"] == []
    with pytest.raises(NativeStateError, match="not active"):
        store.authorize_mutation(store.begin_call("document-a", "analyze.load"))
    with pytest.raises(NativeStateError, match="not active"):
        store.authorize_mutation(store.begin_call("document-a", "analyzer.test"))


def test_closed_scoped_authority_releases_a_failed_background_call() -> None:
    store = NativeDocumentStateStore()
    scope = store.begin_scoped_authority("document-a", "analyze")
    ticket = store.begin_call("document-a", "analyze.mesh")
    store.authorize_mutation(ticket)

    store.end_scoped_authority("document-a", scope)
    store.cancel_mutation(ticket)

    with pytest.raises(NativeStateError, match="not active"):
        store.authorize_mutation(store.begin_call("document-a", "analyze.mesh"))


def test_call_ticket_is_host_generated_and_stale_mutation_is_rejected() -> None:
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")
    ticket = store.begin_call("document-a", "model.feature")

    assert ticket.expected_revision == 0
    assert len(ticket.idempotency_token) == 32
    assert store.authorize_mutation(ticket).duplicate is False
    store.note_structural_change("document-a")

    with pytest.raises(NativeRevisionConflict) as caught:
        store.authorize_mutation(ticket)

    assert caught.value.failure() == {
        "error_code": NATIVE_REVISION_CONFLICT,
        "message": (
            "The document changed after this operation was prepared. "
            "Read its current state and retry."
        ),
        "current_revision": 1,
        "repair": {"retry_from_current_state": True},
    }


def test_aborted_mutation_observation_discards_transient_events() -> None:
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")
    ticket = store.begin_call("document-a", "model.feature")
    store.authorize_mutation(ticket)
    store.begin_mutation_observation(ticket)

    store.note_object_property_change("document-a", "Shape")
    store.note_object_property_change("document-a", "Shape")
    store.cancel_mutation(ticket)

    assert store.current_revision("document-a") == 0
    store.require_vibescript_return_safe("document-a")


def test_committed_mutation_observation_is_one_semantic_revision() -> None:
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")
    ticket = store.begin_call("document-a", "model.feature")
    store.authorize_mutation(ticket)
    store.begin_mutation_observation(ticket)

    store.note_object_property_change("document-a", "Shape")
    store.note_object_property_change("document-a", "Placement")
    prepared = store.prepare_mutation_completion(ticket, {"object": "Box"})
    assert store.commit_mutation_observation(ticket) == 1
    receipt = store.complete_prepared_mutation(prepared)

    assert receipt.revision_before == 0
    assert receipt.revision_after == 1


def test_verified_read_observation_discards_transient_host_events() -> None:
    store = NativeDocumentStateStore()
    ticket = store.begin_call("document-a", "drawing.export")
    store.begin_read_observation(ticket)

    store.note_object_property_change("document-a", "References2D")
    store.note_object_property_change("document-a", "X")
    assert store.current_revision("document-a") == 0
    assert store.complete_read_observation(ticket) == 0

    stale = store.begin_call("document-a", "drawing.export")
    store.begin_read_observation(stale)
    store.note_object_property_change("document-a", "References2D")
    assert store.fail_read_observation(stale) == 1

    with pytest.raises(NativeRevisionConflict):
        store.begin_read_observation(stale)


def test_completed_call_replays_prior_verified_result_before_revision_check() -> None:
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")
    ticket = store.begin_call("document-a", "model.feature")
    store.authorize_mutation(ticket)
    store.note_structural_change("document-a")
    identity = NativeObjectIdentity("document-a", "Box", "PartDesign::Feature")
    receipt = store.complete_mutation(
        ticket,
        {"ok": True, "object": "Box"},
        created=(identity,),
    )
    store.note_structural_change("document-a")

    replay = store.authorize_mutation(ticket)

    assert replay.duplicate is True
    assert replay.prior_verified_result == {"object": "Box", "ok": True}
    assert receipt.revision_before == 0
    assert receipt.revision_after == 1


def test_receipt_records_exact_sorted_object_identities() -> None:
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")
    ticket = store.begin_call("document-a", "model.boolean")
    store.authorize_mutation(ticket)
    first = NativeObjectIdentity("document-a", "Cut", "PartDesign::Feature")
    second = NativeObjectIdentity("document-a", "Tool", "PartDesign::Feature")
    store.note_structural_change("document-a")

    receipt = store.complete_mutation(
        ticket,
        {"ok": True},
        changed=(second, first),
        deleted=(second,),
    )

    assert receipt.changed == (first, second)
    assert store.snapshot("document-a") == {
        "document_uid": "document-a",
        "structural_revision": 1,
        "native_authority": {
            "document_uid": "document-a",
            "active": True,
            "baseline_revision": 0,
            "current_revision": 1,
            "changed": True,
        },
        "recent_receipts": [receipt.summary()],
    }


def test_duplicate_or_conflicting_receipt_evidence_is_rejected() -> None:
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")
    ticket = store.begin_call("document-a", "model.feature")
    store.authorize_mutation(ticket)
    identity = NativeObjectIdentity("document-a", "Box", "PartDesign::Feature")
    with pytest.raises(NativeStateError, match="duplicates"):
        store.complete_mutation(
            ticket,
            {"ok": True},
            created=(identity, identity),
        )

    store.complete_mutation(ticket, {"ok": True}, created=(identity,))
    with pytest.raises(NativeStateError, match="different evidence"):
        store.complete_mutation(ticket, {"ok": False}, created=(identity,))


def test_receipts_and_duplicate_results_share_one_bound() -> None:
    store = NativeDocumentStateStore(receipt_limit=2)
    store.begin_native_authority("document-a")
    tickets = []
    for index in range(3):
        ticket = store.begin_call("document-a", f"model.operation_{index}")
        tickets.append(ticket)
        store.authorize_mutation(ticket)
        store.note_structural_change("document-a")
        store.complete_mutation(ticket, {"index": index})

    assert len(store.snapshot("document-a")["recent_receipts"]) == 2
    with pytest.raises(NativeRevisionConflict):
        store.authorize_mutation(tickets[0])
    assert store.authorize_mutation(tickets[-1]).prior_verified_result == {"index": 2}


def test_verified_result_size_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(state_module, "MAX_VERIFIED_RESULT_JSON_BYTES", 10)
    store = NativeDocumentStateStore()
    store.begin_native_authority("document-a")
    ticket = store.begin_call("document-a", "model.feature")
    store.authorize_mutation(ticket)
    with pytest.raises(NativeStateError, match="bounded size"):
        store.complete_mutation(
            ticket,
            {"result": "too large"},
        )


def test_native_authority_epoch_allows_only_an_unchanged_return() -> None:
    store = NativeDocumentStateStore()
    store.note_structural_change("document-a")

    assert store.begin_native_authority("document-a") == {
        "document_uid": "document-a",
        "active": True,
        "baseline_revision": 1,
        "current_revision": 1,
        "changed": False,
    }
    store.require_vibescript_return_safe("document-a")
    assert store.end_native_authority("document-a")["active"] is False

    store.begin_native_authority("document-a")
    store.note_structural_change("document-a")
    with pytest.raises(NativeAuthorityConflict) as caught:
        store.require_vibescript_return_safe("document-a")
    assert caught.value.failure() == {
        "error_code": NATIVE_AUTHORITY_CONFLICT,
        "message": (
            "This document changed after Native authority began. Discard the "
            "Native epoch or create a new VibeScript source before returning "
            "to VibeScript authority."
        ),
        "current_revision": 2,
        "repair": {"requires_explicit_authority_reset": True},
    }


def test_native_authority_and_receipts_round_trip_exactly() -> None:
    original = NativeDocumentStateStore()
    original.note_structural_change("document-a")
    original.begin_native_authority("document-a")
    ticket = original.begin_call("document-a", "model.feature")
    original.authorize_mutation(ticket)
    original.note_structural_change("document-a")
    identity = NativeObjectIdentity("document-a", "Box", "PartDesign::Feature")
    original.complete_mutation(
        ticket,
        {"ok": True, "object": "Box"},
        created=(identity,),
    )
    payload = original.export_document("document-a")

    restored = NativeDocumentStateStore()
    restored.restore_document("document-a", payload)

    assert restored.export_document("document-a") == payload
    assert restored.authorize_mutation(ticket).prior_verified_result == {
        "object": "Box",
        "ok": True,
    }
    with pytest.raises(NativeAuthorityConflict):
        restored.require_vibescript_return_safe("document-a")


def test_restore_rejects_wrong_document_or_live_changes() -> None:
    original = NativeDocumentStateStore()
    payload = original.export_document("document-a")
    restored = NativeDocumentStateStore()

    with pytest.raises(NativeStateError, match="another document"):
        restored.restore_document("document-b", payload)

    restored.note_structural_change("document-a")
    with pytest.raises(NativeStateError, match="live document changes"):
        restored.restore_document("document-a", payload)


def test_document_observer_filters_presentation_before_revision(
    monkeypatch,
) -> None:
    import VibeCADGui as gui

    store = NativeDocumentStateStore()

    class Service:
        def note_native_object_property_change(self, obj, property_name):
            store.note_object_property_change(obj.Document.Uid, property_name)

        def invalidate_vibescript_reference_snapshots(self, _obj):
            return None

        def native_document_state(self):
            return {"native_authority": {"active": False}}

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    obj = SimpleNamespace(
        Document=SimpleNamespace(Uid="document-a", Restoring=False),
    )
    observer = gui._VibeCADDocumentObserver()

    observer.slotChangedObject(obj, "Visibility")
    observer.slotChangedObject(obj, "Placement")

    assert store.current_revision("document-a") == 1


def test_gui_document_observer_forwards_visibility_changes(monkeypatch) -> None:
    import VibeCADGui as gui

    changed = []
    service = SimpleNamespace(
        note_native_object_property_change=(
            lambda obj, property_name: changed.append((obj, property_name))
        ),
    )
    monkeypatch.setattr(gui, "get_service", lambda: service)
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    obj = SimpleNamespace(
        Document=SimpleNamespace(Uid="document-a", Restoring=False),
    )
    view_provider = SimpleNamespace(Object=obj)

    gui._VibeCADGuiDocumentObserver().slotChangedObject(
        view_provider,
        "Visibility",
    )

    assert changed == [(obj, "Visibility")]


def test_document_observer_counts_create_and_delete(monkeypatch) -> None:
    import VibeCADGui as gui

    store = NativeDocumentStateStore()

    class Service:
        def note_native_object_created(self, obj):
            store.note_structural_change(obj.Document.Uid)

        def note_native_object_deleted(self, obj):
            store.note_structural_change(obj.Document.Uid)

        def native_document_state(self):
            return {"native_authority": {"active": False}}

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    obj = SimpleNamespace(
        Document=SimpleNamespace(Uid="document-a", Restoring=False),
    )
    observer = gui._VibeCADDocumentObserver()

    observer.slotCreatedObject(obj)
    observer.slotDeletedObject(obj)

    assert store.current_revision("document-a") == 2


def test_document_observer_coalesces_authority_selector_refreshes(monkeypatch) -> None:
    import VibeCADGui as gui

    scheduled = []
    refreshed = []

    class Service:
        @staticmethod
        def note_native_object_property_change(_obj, _property_name):
            return None

        @staticmethod
        def native_document_state():
            return {"native_authority": {"active": True}}

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    monkeypatch.setattr(
        gui,
        "_queue_zero_delay_callback",
        lambda callback: scheduled.append(callback),
    )
    monkeypatch.setattr(
        gui,
        "_refresh_authoring_mode_selector",
        lambda: refreshed.append(True),
    )
    monkeypatch.setattr(gui, "_native_authority_selector_refresh_scheduled", False)
    obj = SimpleNamespace(
        Document=SimpleNamespace(Uid="document-a", Restoring=False),
    )
    observer = gui._VibeCADDocumentObserver()

    observer.slotChangedObject(obj, "_LinkTouched")
    observer.slotChangedObject(obj, "_LinkTouched")

    assert len(scheduled) == 1
    assert refreshed == []
    scheduled.pop()()
    assert refreshed == [True]


def test_service_coalesces_atomic_document_change_bookkeeping(monkeypatch) -> None:
    import threading

    from VibeCADCore import VibeCADService

    service = object.__new__(VibeCADService)
    service._native_document_states = NativeDocumentStateStore()
    service._document_change_batch_lock = threading.RLock()
    service._deferred_document_changes = {}
    invalidations = []
    metadata_syncs = []
    monkeypatch.setattr(
        service,
        "_invalidate_native_read_contexts",
        lambda uid: invalidations.append(uid),
    )
    monkeypatch.setattr(
        service,
        "_sync_native_authority_metadata_if_active",
        lambda uid: metadata_syncs.append(uid),
    )
    document = SimpleNamespace(Uid="document-a")
    obj = SimpleNamespace(Document=document)

    service.begin_document_change_batch("document-a")
    for _index in range(100):
        service.note_native_object_created(obj)
        service.note_native_object_property_change(obj, "Shape")
    assert service._native_document_states.current_revision("document-a") == 0
    service.end_document_change_batch("document-a")

    assert service._native_document_states.current_revision("document-a") == 1
    assert invalidations == ["document-a"]
    assert metadata_syncs == ["document-a"]


def test_service_document_change_batches_are_nested_and_exception_safe(
    monkeypatch,
) -> None:
    import threading

    from VibeCADCore import VibeCADService
    from VibeCADDocumentChangeBatch import document_change_batch_active

    service = object.__new__(VibeCADService)
    service._native_document_states = NativeDocumentStateStore()
    service._document_change_batch_lock = threading.RLock()
    service._deferred_document_changes = {}
    monkeypatch.setattr(service, "_invalidate_native_read_contexts", lambda _uid: None)
    monkeypatch.setattr(
        service,
        "_sync_native_authority_metadata_if_active",
        lambda _uid: (_ for _ in ()).throw(RuntimeError("metadata failed")),
    )
    obj = SimpleNamespace(Document=SimpleNamespace(Uid="document-b"))

    service.begin_document_change_batch("document-b")
    service.begin_document_change_batch("document-b")
    service.note_native_object_created(obj)
    service.end_document_change_batch("document-b")
    assert document_change_batch_active("document-b") is True
    assert service._native_document_states.current_revision("document-b") == 0

    with pytest.raises(RuntimeError, match="metadata failed"):
        service.end_document_change_batch("document-b")

    assert document_change_batch_active("document-b") is False
    assert service.document_change_batch_active("document-b") is False
    assert service._native_document_states.current_revision("document-b") == 1


def test_service_discards_bookkeeping_for_rolled_back_document_batch(
    monkeypatch,
) -> None:
    import threading

    from VibeCADCore import VibeCADService
    from VibeCADDocumentChangeBatch import document_change_batch_active

    service = object.__new__(VibeCADService)
    service._native_document_states = NativeDocumentStateStore()
    service._document_change_batch_lock = threading.RLock()
    service._deferred_document_changes = {}
    invalidations = []
    metadata_syncs = []
    monkeypatch.setattr(
        service,
        "_invalidate_native_read_contexts",
        lambda uid: invalidations.append(uid),
    )
    monkeypatch.setattr(
        service,
        "_sync_native_authority_metadata_if_active",
        lambda uid: metadata_syncs.append(uid),
    )
    obj = SimpleNamespace(Document=SimpleNamespace(Uid="document-c"))

    service.begin_document_change_batch("document-c")
    service.note_native_object_created(obj)
    service.note_native_object_property_change(obj, "Shape")
    service.end_document_change_batch("document-c", commit=False)

    assert document_change_batch_active("document-c") is False
    assert service.document_change_batch_active("document-c") is False
    assert service._native_document_states.current_revision("document-c") == 0
    assert invalidations == []
    assert metadata_syncs == []


def test_document_change_batch_reports_the_outermost_commit_outcome() -> None:
    from VibeCADDocumentChangeBatch import (
        begin_document_change_batch,
        end_document_change_batch,
        register_document_change_batch_finished,
    )

    outcomes = []
    register_document_change_batch_finished(
        lambda uid, committed: outcomes.append((uid, committed))
        if uid == "document-outcome"
        else None
    )

    begin_document_change_batch("document-outcome")
    begin_document_change_batch("document-outcome")
    assert end_document_change_batch("document-outcome", commit=False) is False
    assert outcomes == []
    assert end_document_change_batch("document-outcome") is True

    assert outcomes == [("document-outcome", False)]


def test_document_observer_batches_dependency_invalidation_and_stale_scans(
    monkeypatch,
) -> None:
    import VibeCADGui as gui
    import VibeCADVibeScriptDomainPublication as publication

    class Source:
        Document = SimpleNamespace(Uid="document-batched", Restoring=False)

    source = Source()
    batch_active = [True]
    invalidated = []
    stale_scans = []
    refreshed = []

    class Service:
        @staticmethod
        def note_native_object_property_change(_obj, _property_name):
            return None

        @staticmethod
        def invalidate_vibescript_reference_snapshots_many(objects):
            invalidated.append(tuple(objects))

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    monkeypatch.setattr(
        gui,
        "document_change_batch_active",
        lambda uid: batch_active[0] and uid == "document-batched",
    )
    monkeypatch.setattr(
        publication,
        "mark_programs_stale_from_sources",
        lambda changes, **_kwargs: stale_scans.append(tuple(changes))
        or ["DependentOutput"],
    )
    monkeypatch.setattr(
        gui,
        "_schedule_assistant_document_refresh",
        lambda: refreshed.append(True),
    )
    gui._deferred_vibescript_dependency_changes.clear()
    observer = gui._VibeCADDocumentObserver()

    for _index in range(100):
        observer.slotChangedObject(source, "Shape")

    assert invalidated == []
    assert stale_scans == []

    batch_active[0] = False
    gui._finish_vibescript_dependency_batch("document-batched", True)

    assert invalidated == [(source,)]
    assert stale_scans == [((source, "Shape"),)]
    assert refreshed == [True]


def test_document_observer_discards_dependency_work_after_rollback(
    monkeypatch,
) -> None:
    import VibeCADGui as gui
    import VibeCADVibeScriptDomainPublication as publication

    class Source:
        Document = SimpleNamespace(Uid="document-rollback", Restoring=False)

    source = Source()
    invalidated = []
    stale_scans = []

    class Service:
        @staticmethod
        def note_native_object_property_change(_obj, _property_name):
            return None

        @staticmethod
        def invalidate_vibescript_reference_snapshots_many(objects):
            invalidated.append(tuple(objects))

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    monkeypatch.setattr(gui, "document_change_batch_active", lambda _uid: True)
    monkeypatch.setattr(
        publication,
        "mark_programs_stale_from_sources",
        lambda changes, **_kwargs: stale_scans.append(tuple(changes)) or [],
    )
    gui._deferred_vibescript_dependency_changes.clear()

    gui._VibeCADDocumentObserver().slotChangedObject(source, "Shape")
    gui._finish_vibescript_dependency_batch("document-rollback", False)

    assert invalidated == []
    assert stale_scans == []


def test_bulk_stale_propagation_scans_each_document_once(monkeypatch) -> None:
    import VibeCADVibeScriptDomainPublication as publication

    class Document:
        Uid = "document-bulk-stale"

        def __init__(self) -> None:
            self.object_reads = 0
            self.outputs = []

        @property
        def Objects(self):
            self.object_reads += 1
            return list(self.outputs)

    document = Document()
    first_source = SimpleNamespace(Name="SourceA", InList=[])
    second_source = SimpleNamespace(Name="SourceB", InList=[])
    output = SimpleNamespace(
        Name="DependentOutput",
        TypeId="Part::Feature",
        Document=document,
        PropertiesList=[
            publication.PROP_INPUT_OBJECTS,
            publication.contracts.PROP_PROGRAM_ID,
            publication.contracts.PROP_PROGRAM_DOMAIN,
            publication.contracts.PROP_PROGRAM_REVISION,
            publication.reference_contracts.PROP_DERIVED_STATE,
        ],
        VibeCADVibeScriptInputObjects=[first_source, second_source],
        VibeCADVibeScriptNestedInputObjects=[],
        VibeCADVibeScriptProgramId="program-a",
        VibeCADVibeScriptDomain="part",
        VibeCADVibeScriptRevision="revision-a",
        VibeCADVibeScriptDerivedState="accepted",
    )
    first_source.InList = [output]
    second_source.InList = [output]
    document.outputs = [output]
    marked = []
    monkeypatch.setattr(
        publication.reference_contracts,
        "mark_stale",
        lambda obj, revision, reason: marked.append((obj, revision, reason)),
    )

    result = publication.mark_programs_stale_from_sources(
        ((first_source, "Shape"), (second_source, "Placement"))
    )

    assert result == ["DependentOutput"]
    assert document.object_reads == 1
    assert len(marked) == 1
    assert marked[0][0] is output


def test_state_module_has_no_ui_activation_or_tool_execution_api() -> None:
    public_names = {name for name in vars(state_module) if not name.startswith("_")}
    forbidden = ("activate", "switch", "run_command", "execute")
    assert not any(
        fragment in name.lower()
        for name in public_names
        for fragment in forbidden
    )
