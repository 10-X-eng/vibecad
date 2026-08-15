# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact participation in a provisional human-owned New Sketch transaction."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADEditState import active_edit_object
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMutation import (
    NATIVE_TRANSACTION_ACTIVE,
    NativeMutationDraft,
    NativeMutationError,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


class _ProvisionalSketchTaskMutation:
    """One savepoint inside the exact transaction owned by a New Sketch task."""

    creates_undo_entry = False

    def __init__(self, document: Any, sketch: Any, _name: str) -> None:
        self._document = document
        self._sketch = sketch
        self._transaction_id = self._require_exact_boundary()
        capture = getattr(sketch, "captureMutationState", None)
        if not callable(capture):
            raise NativeMutationError(
                NATIVE_TRANSACTION_ACTIVE,
                "The active New Sketch task cannot create an exact mutation savepoint.",
            )
        self._savepoint = capture()
        self._closed = False

    def _require_exact_boundary(self) -> int:
        document = self._document
        sketch = self._sketch
        transaction_id = int(document.getBookedTransactionID() or 0)
        provisional = getattr(
            document,
            "isProvisionallyEnrolledInTimelineByCurrentTransaction",
            None,
        )
        if (
            transaction_id == 0
            or not bool(document.HasPendingTransaction)
            or getattr(sketch, "Document", None) is not document
            or document.getObject(str(getattr(sketch, "Name", "") or "")) is not sketch
            or str(getattr(sketch, "TypeId", "") or "")
            != "Sketcher::SketchObject"
            or active_edit_object() is not sketch
            or not callable(provisional)
            or not bool(provisional(sketch))
        ):
            raise NativeMutationError(
                NATIVE_TRANSACTION_ACTIVE,
                "Only the exact provisional New Sketch task transaction can be joined.",
            )
        return transaction_id

    def _require_same_transaction(self) -> None:
        if self._require_exact_boundary() != self._transaction_id:
            raise NativeMutationError(
                NATIVE_TRANSACTION_ACTIVE,
                "The provisional New Sketch transaction changed during the operation.",
            )

    def commit(self) -> None:
        if self._closed:
            return
        self._require_same_transaction()
        self._savepoint = None
        self._closed = True

    def abort(self) -> None:
        if self._closed:
            return
        self._require_same_transaction()
        restore = getattr(self._sketch, "restoreMutationState", None)
        if not callable(restore) or self._savepoint is None:
            raise RuntimeError("The exact Sketch mutation savepoint is unavailable")
        restore(self._savepoint)
        result = self._document.recompute((self._sketch,), True, True)
        if result is False:
            raise RuntimeError("The restored Sketch failed to recompute")
        self._require_same_transaction()
        self._savepoint = None
        self._closed = True


def _active_task_factory(sketch: Any) -> Callable[[Any, str], Any]:
    return lambda document, name: _ProvisionalSketchTaskMutation(
        document,
        sketch,
        name,
    )


def run_active_sketch_mutation(
    context: NativeRuntimeContext,
    *,
    ticket: NativeCallTicket,
    transaction_name: str,
    mutate: Callable[[Any], NativeMutationDraft],
    verify: Callable[[Any, NativeMutationDraft], Mapping[str, Any]],
) -> dict[str, Any]:
    """Run against the human-opened Sketch, borrowing only a provisional task."""

    sketch = active_edit_object()
    return run_immediate_mutation(
        context,
        ticket=ticket,
        transaction_name=transaction_name,
        mutate=mutate,
        verify=verify,
        active_transaction_factory=_active_task_factory(sketch),
    )
