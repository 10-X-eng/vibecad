# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import dataclass

import pytest

from VibeCADNativeState import NativeDocumentStateStore, NativeObjectIdentity
from VibeCADNativeUndo import (
    NATIVE_UNDO_FAILED,
    NATIVE_UNDO_UNAVAILABLE,
    NativeAssistantUndoLedger,
    NativeUndoError,
)


class _Object:
    def __init__(self, document, name: str, type_id: str = "PartDesign::Feature"):
        self.Document = document
        self.Name = name
        self.TypeId = type_id


@dataclass
class _HistoryItem:
    name: str
    before: dict[str, str]
    after: dict[str, str]


class _Document:
    Uid = "document-a"
    Name = "DocumentA"
    HasPendingTransaction = False

    def __init__(self, *, history_limit: int | None = None) -> None:
        self.objects: dict[str, _Object] = {}
        self.history: list[_HistoryItem] = []
        self.history_index = 0
        self.history_limit = history_limit
        self.booked_transaction = 0
        self.corrupt_next_undo = False

    @property
    def UndoCount(self) -> int:
        return self.history_index

    @property
    def UndoNames(self) -> list[str]:
        return [item.name for item in reversed(self.history[: self.history_index])]

    def getBookedTransactionID(self) -> int:
        return self.booked_transaction

    def getObject(self, name: str):
        return self.objects.get(name)

    def snapshot(self) -> dict[str, str]:
        return {name: obj.TypeId for name, obj in self.objects.items()}

    def restore(self, snapshot: dict[str, str]) -> None:
        self.objects = {
            name: _Object(self, name, type_id) for name, type_id in snapshot.items()
        }

    def commit(self, name: str, before: dict[str, str]) -> None:
        self.history = self.history[: self.history_index]
        self.history.append(_HistoryItem(name, before, self.snapshot()))
        self.history_index += 1
        if self.history_limit is not None and len(self.history) > self.history_limit:
            overflow = len(self.history) - self.history_limit
            self.history = self.history[overflow:]
            self.history_index -= overflow

    def undo(self) -> None:
        item = self.history[self.history_index - 1]
        self.history_index -= 1
        if not self.corrupt_next_undo:
            self.restore(item.before)
        self.corrupt_next_undo = False

    def redo(self) -> None:
        item = self.history[self.history_index]
        self.restore(item.after)
        self.history_index += 1


def _host(*, history_limit: int | None = None):
    state = NativeDocumentStateStore()
    state.begin_native_authority("document-a")
    document = _Document(history_limit=history_limit)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("run-a")
    return state, document, ledger


def _record_create(state, document, ledger, object_name: str):
    transaction_name = f"Create {object_name}"
    checkpoint = ledger.checkpoint(document)
    before = document.snapshot()
    ticket = state.begin_call(document.Uid, "model.feature")
    state.authorize_mutation(ticket)
    state.begin_mutation_observation(ticket)
    obj = _Object(document, object_name)
    document.objects[object_name] = obj
    document.commit(transaction_name, before)
    state.note_structural_change(document.Uid)
    state.commit_mutation_observation(ticket)
    identity = NativeObjectIdentity(document.Uid, obj.Name, obj.TypeId)
    receipt = state.complete_mutation(
        ticket,
        {"object": identity.summary()},
        created=(identity,),
    )
    assert ledger.record_commit(document, transaction_name, checkpoint, receipt)
    return receipt


def _undo(state, document, ledger, ticket=None):
    call_ticket = ticket or state.begin_call(document.Uid, "document.undo")
    execution = ledger.undo_latest(
        ticket=call_ticket,
        document=document,
        state=state,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
    )
    return call_ticket, execution


def test_local_undo_removes_only_the_latest_operation_from_this_run() -> None:
    state, document, ledger = _host()
    _record_create(state, document, ledger, "First")
    _record_create(state, document, ledger, "Second")

    assert ledger.available(document, state)["operation"]["capability"] == "model.feature"
    _ticket, second = _undo(state, document, ledger)
    assert second.duplicate is False
    assert document.getObject("First") is not None
    assert document.getObject("Second") is None
    assert document.UndoNames == ["Create First"]
    assert ledger.available(document, state)["available"] is True

    first_ticket, first = _undo(state, document, ledger)
    assert first.result["undo_available"] is False
    assert document.getObject("First") is None
    assert document.UndoCount == 0
    assert ledger.available(document, state) == {"available": False}

    _same_ticket, replay = _undo(state, document, ledger, first_ticket)
    assert replay.duplicate is True
    assert replay.result == first.result
    assert document.UndoCount == 0


def test_human_history_on_top_is_never_undone() -> None:
    state, document, ledger = _host()
    _record_create(state, document, ledger, "NativeBox")
    before = document.snapshot()
    document.objects["HumanBox"] = _Object(document, "HumanBox")
    document.commit("Human change", before)
    state.note_structural_change(document.Uid)

    with pytest.raises(NativeUndoError) as caught:
        _undo(state, document, ledger)

    assert caught.value.error_code == NATIVE_UNDO_UNAVAILABLE
    assert document.UndoNames[0] == "Human change"
    assert document.getObject("HumanBox") is not None


def test_revision_guard_detects_human_undo_redo_even_when_history_matches() -> None:
    state, document, ledger = _host()
    _record_create(state, document, ledger, "NativeBox")
    document.undo()
    state.note_structural_change(document.Uid)
    document.redo()
    state.note_structural_change(document.Uid)
    assert document.UndoNames == ["Create NativeBox"]

    with pytest.raises(NativeUndoError, match="history changed"):
        _undo(state, document, ledger)

    assert document.getObject("NativeBox") is not None


def test_failed_undo_postcondition_is_redone_without_revision_change() -> None:
    state, document, ledger = _host()
    _record_create(state, document, ledger, "NativeBox")
    revision_before = state.current_revision(document.Uid)
    document.corrupt_next_undo = True

    with pytest.raises(NativeUndoError) as caught:
        _undo(state, document, ledger)

    assert caught.value.error_code == NATIVE_UNDO_FAILED
    assert document.getObject("NativeBox") is not None
    assert document.UndoNames == ["Create NativeBox"]
    assert state.current_revision(document.Uid) == revision_before
    assert ledger.available(document, state)["available"] is True


def test_new_run_and_end_run_discard_local_undo_ownership() -> None:
    state, document, ledger = _host()
    _record_create(state, document, ledger, "NativeBox")

    ledger.begin_run("run-b")
    assert ledger.available(document, state) == {"available": False}
    ledger.end_run("run-b")

    with pytest.raises(NativeUndoError, match="No assistant run"):
        ledger.checkpoint(document)


def test_record_refuses_a_commit_that_is_not_the_exact_top_history_entry() -> None:
    state, document, ledger = _host()
    checkpoint = ledger.checkpoint(document)
    receipt = _record_create(state, document, ledger, "NativeBox")

    assert ledger.record_commit(document, "Different name", checkpoint, receipt) is False
    assert ledger.available(document, state) == {"available": False}


def test_local_undo_remains_safe_at_the_host_history_limit() -> None:
    state, document, ledger = _host(history_limit=2)
    _record_create(state, document, ledger, "First")
    _record_create(state, document, ledger, "Second")
    _record_create(state, document, ledger, "Third")

    assert document.UndoCount == 2
    assert document.UndoNames == ["Create Third", "Create Second"]
    assert ledger.available(document, state)["available"] is True

    _ticket, third = _undo(state, document, ledger)
    assert third.result["undo_available"] is True
    assert document.UndoNames == ["Create Second"]
    assert document.getObject("Third") is None
    assert ledger.available(document, state)["available"] is True

    _ticket, second = _undo(state, document, ledger)
    assert second.result["undo_available"] is False
    assert document.UndoNames == []
    assert document.getObject("Second") is None
    assert document.getObject("First") is not None
    assert ledger.available(document, state) == {"available": False}


def test_intervening_human_history_discards_older_assistant_ownership() -> None:
    state, document, ledger = _host()
    _record_create(state, document, ledger, "First")
    before = document.snapshot()
    document.objects["Human"] = _Object(document, "Human")
    document.commit("Human change", before)
    state.note_structural_change(document.Uid)
    _record_create(state, document, ledger, "Second")

    _ticket, second = _undo(state, document, ledger)
    assert second.result["undo_available"] is False
    assert document.UndoNames[0] == "Human change"
    assert document.getObject("Human") is not None
    assert ledger.available(document, state) == {"available": False}
