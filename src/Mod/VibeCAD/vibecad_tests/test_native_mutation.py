# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from copy import deepcopy

import pytest

from VibeCADNativeMutation import (
    NATIVE_EXECUTION_FAILED,
    NATIVE_POSTCONDITION_FAILED,
    NATIVE_RECOMPUTE_FAILED,
    NATIVE_TRANSACTION_ACTIVE,
    NativeMutationDraft,
    NativeMutationError,
    NativeMutationRunner,
)
from VibeCADNativeState import (
    NativeDocumentStateStore,
    NativeObjectIdentity,
    NativeRevisionConflict,
)


class _Object:
    def __init__(self, document, name: str, type_id: str = "PartDesign::Feature"):
        self.Document = document
        self.Name = name
        self.TypeId = type_id


class _Document:
    def __init__(self) -> None:
        self.Uid = "document-a"
        self.Name = "DocumentA"
        self.HasPendingTransaction = False
        self.booked_transaction = 0
        self.open = True
        self.objects: dict[str, _Object] = {}
        self.recompute_calls: list[tuple[str, ...]] = []
        self.recompute_fails = False
        self.history: list[tuple[dict[str, str], dict[str, str]]] = []
        self.history_index = 0

    def getBookedTransactionID(self) -> int:
        return self.booked_transaction

    def getObject(self, name: str):
        return self.objects.get(name)

    def snapshot(self) -> dict[str, str]:
        return {name: obj.TypeId for name, obj in self.objects.items()}

    def restore(self, snapshot: dict[str, str]) -> None:
        self.objects = {
            name: _Object(self, name, type_id)
            for name, type_id in snapshot.items()
        }

    def recompute(self, targets, force: bool, check_cycles: bool):
        assert force is True
        assert check_cycles is True
        self.recompute_calls.append(tuple(target.Name for target in targets))
        if self.recompute_fails:
            raise RuntimeError("solver failed")
        return len(targets)

    def undo(self) -> None:
        before, _after = self.history[self.history_index - 1]
        self.restore(before)
        self.history_index -= 1

    def redo(self) -> None:
        _before, after = self.history[self.history_index]
        self.restore(after)
        self.history_index += 1


class _Transaction:
    def __init__(self, document: _Document, _name: str, owner) -> None:
        self.document = document
        self.owner = owner
        self.before = deepcopy(document.snapshot())
        document.booked_transaction = 1
        document.HasPendingTransaction = True

    def commit(self) -> None:
        self.owner.commits += 1
        self.document.history = self.document.history[: self.document.history_index]
        self.document.history.append((self.before, self.document.snapshot()))
        self.document.history_index += 1
        self.document.booked_transaction = 0
        self.document.HasPendingTransaction = False

    def abort(self) -> None:
        self.owner.aborts += 1
        self.document.restore(self.before)
        self.document.booked_transaction = 0
        self.document.HasPendingTransaction = False


class _Transactions:
    def __init__(self) -> None:
        self.opens = 0
        self.commits = 0
        self.aborts = 0

    def __call__(self, document: _Document, name: str) -> _Transaction:
        self.opens += 1
        return _Transaction(document, name, self)


def _host():
    state = NativeDocumentStateStore()
    state.begin_native_authority("document-a")
    document = _Document()
    transactions = _Transactions()
    runner = NativeMutationRunner(
        state,
        transaction_factory=transactions,
        document_is_live=lambda target: target.open,
    )
    ticket = state.begin_call("document-a", "model.feature")
    return state, document, transactions, runner, ticket


def _successful_mutation(state, document):
    created = _Object(document, "Box")
    document.objects[created.Name] = created
    state.note_structural_change(document.Uid)
    return NativeMutationDraft(
        value=created,
        recompute_targets=(created,),
        created=(NativeObjectIdentity(document.Uid, created.Name, created.TypeId),),
    )


def _verify(document, draft):
    assert document.getObject(draft.value.Name) is draft.value
    return {"object": draft.created[0].summary(), "valid": True}


def test_success_is_one_transaction_revision_and_exact_recompute() -> None:
    state, document, transactions, runner, ticket = _host()

    execution = runner.run(
        ticket=ticket,
        document=document,
        transaction_name="Create exact feature",
        reauthorize_turn=lambda: None,
        mutate=lambda target: _successful_mutation(state, target),
        verify=_verify,
    )

    assert execution.duplicate is False
    assert execution.result["object"]["object_name"] == "Box"
    assert execution.receipt.revision_before == 0
    assert execution.receipt.revision_after == 1
    assert document.recompute_calls == [("Box",)]
    assert (transactions.opens, transactions.commits, transactions.aborts) == (1, 1, 0)


def test_committed_operation_is_one_exact_undo_and_redo_step() -> None:
    state, document, _transactions, runner, ticket = _host()
    before = document.snapshot()
    runner.run(
        ticket=ticket,
        document=document,
        transaction_name="Create exact feature",
        reauthorize_turn=lambda: None,
        mutate=lambda target: _successful_mutation(state, target),
        verify=_verify,
    )
    committed = document.snapshot()

    assert len(document.history) == 1
    document.undo()
    assert document.snapshot() == before
    document.redo()
    assert document.snapshot() == committed


def test_stale_ticket_is_rejected_before_transaction_open() -> None:
    state, document, transactions, runner, ticket = _host()
    state.note_structural_change(document.Uid)

    with pytest.raises(NativeRevisionConflict):
        runner.run(
            ticket=ticket,
            document=document,
            transaction_name="Never starts",
            reauthorize_turn=lambda: None,
            mutate=lambda target: _successful_mutation(state, target),
            verify=_verify,
        )

    assert transactions.opens == 0


def test_existing_transaction_is_never_nested() -> None:
    _state, document, transactions, runner, ticket = _host()
    document.booked_transaction = 9
    document.HasPendingTransaction = True

    with pytest.raises(NativeMutationError) as caught:
        runner.run(
            ticket=ticket,
            document=document,
            transaction_name="Never starts",
            reauthorize_turn=lambda: None,
            mutate=lambda _target: pytest.fail("mutation ran"),
            verify=_verify,
        )

    assert caught.value.error_code == NATIVE_TRANSACTION_ACTIVE
    assert transactions.opens == 0


def test_turn_reauthorization_failure_prevents_preflight_and_transaction() -> None:
    _state, document, transactions, runner, ticket = _host()

    def reject_changed_turn():
        raise RuntimeError("surface changed")

    with pytest.raises(RuntimeError, match="surface changed"):
        runner.run(
            ticket=ticket,
            document=document,
            transaction_name="Never starts",
            reauthorize_turn=reject_changed_turn,
            mutate=lambda _target: pytest.fail("mutation ran"),
            verify=_verify,
        )

    assert transactions.opens == 0


def test_turn_change_during_mutation_aborts_before_commit() -> None:
    state, document, transactions, runner, ticket = _host()
    checks = 0

    class _SurfaceChanged(RuntimeError):
        def failure(self):
            return {
                "error_code": "NATIVE_SURFACE_CHANGED",
                "message": "Resume from the new ribbon.",
                "current_surface": "mesh",
            }

    def reauthorize():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise _SurfaceChanged()

    with pytest.raises(_SurfaceChanged):
        runner.run(
            ticket=ticket,
            document=document,
            transaction_name="Abort changed surface",
            reauthorize_turn=reauthorize,
            mutate=lambda target: _successful_mutation(state, target),
            verify=_verify,
        )

    assert checks == 2
    assert document.objects == {}
    assert (transactions.opens, transactions.commits, transactions.aborts) == (1, 0, 1)
    assert state.current_revision(document.Uid) == 0


@pytest.mark.parametrize(
    ("failure", "code"),
    (
        ("execute", NATIVE_EXECUTION_FAILED),
        ("recompute", NATIVE_RECOMPUTE_FAILED),
        ("verify", NATIVE_POSTCONDITION_FAILED),
    ),
)
def test_every_precommit_failure_aborts_without_dirty_authority(
    failure: str,
    code: str,
) -> None:
    state, document, transactions, runner, ticket = _host()

    def mutate(target):
        draft = _successful_mutation(state, target)
        if failure == "execute":
            raise RuntimeError("execution failed")
        if failure == "recompute":
            document.recompute_fails = True
        return draft

    def verify(target, draft):
        if failure == "verify":
            raise RuntimeError("invalid geometry")
        return _verify(target, draft)

    with pytest.raises(NativeMutationError) as caught:
        runner.run(
            ticket=ticket,
            document=document,
            transaction_name="Abort exact feature",
            reauthorize_turn=lambda: None,
            mutate=mutate,
            verify=verify,
        )

    assert caught.value.error_code == code
    assert document.objects == {}
    assert (transactions.commits, transactions.aborts) == (0, 1)
    assert state.current_revision(document.Uid) == 0
    state.require_vibescript_return_safe(document.Uid)


def test_duplicate_retry_returns_verified_result_without_mutation() -> None:
    state, document, transactions, runner, ticket = _host()
    first = runner.run(
        ticket=ticket,
        document=document,
        transaction_name="Create exact feature",
        reauthorize_turn=lambda: None,
        mutate=lambda target: _successful_mutation(state, target),
        verify=_verify,
    )

    replay = runner.run(
        ticket=ticket,
        document=document,
        transaction_name="Create exact feature",
        reauthorize_turn=lambda: None,
        mutate=lambda _target: pytest.fail("duplicate mutation ran"),
        verify=lambda _document, _draft: pytest.fail("duplicate verification ran"),
    )

    assert replay.result == first.result
    assert replay.duplicate is True
    assert replay.receipt is None
    assert transactions.opens == 1
