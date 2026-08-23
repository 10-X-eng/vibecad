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

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    obj = SimpleNamespace(
        Document=SimpleNamespace(Uid="document-a", Restoring=False),
    )
    observer = gui._VibeCADDocumentObserver()

    observer.slotChangedObject(obj, "Visibility")
    observer.slotChangedObject(obj, "Placement")

    assert store.current_revision("document-a") == 1


def test_document_observer_counts_create_and_delete(monkeypatch) -> None:
    import VibeCADGui as gui

    store = NativeDocumentStateStore()

    class Service:
        def note_native_object_created(self, obj):
            store.note_structural_change(obj.Document.Uid)

        def note_native_object_deleted(self, obj):
            store.note_structural_change(obj.Document.Uid)

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    monkeypatch.setattr(gui.App, "isRestoring", lambda: False, raising=False)
    obj = SimpleNamespace(
        Document=SimpleNamespace(Uid="document-a", Restoring=False),
    )
    observer = gui._VibeCADDocumentObserver()

    observer.slotCreatedObject(obj)
    observer.slotDeletedObject(obj)

    assert store.current_revision("document-a") == 2


def test_state_module_has_no_ui_activation_or_tool_execution_api() -> None:
    public_names = {name for name in vars(state_module) if not name.startswith("_")}
    forbidden = ("activate", "switch", "run_command", "execute")
    assert not any(
        fragment in name.lower()
        for name in public_names
        for fragment in forbidden
    )
