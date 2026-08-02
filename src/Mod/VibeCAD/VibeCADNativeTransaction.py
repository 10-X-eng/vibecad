# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transaction ownership for VibeCAD's immediate native GUI commands."""

from __future__ import annotations

from typing import Any

import FreeCAD as App

if App.GuiUp:
    from PySide import QtCore
else:
    QtCore = None


_deferred_undo_mode_restores: dict[str, tuple[Any, int]] = {}
_retained_transaction_closes: dict[
    tuple[str, int],
    "_OwnedDocumentTransaction",
] = {}
_transaction_retry_queued = False


def _document_key(document: Any) -> str:
    return str(document.Name)


def _document_is_open(document: Any) -> bool:
    try:
        return App.getDocument(document.Name) == document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _restore_ready_undo_modes() -> None:
    """Restore only documents that have reached a transaction-free boundary."""

    for key, (document, previous_mode) in tuple(
        _deferred_undo_mode_restores.items()
    ):
        if not _document_is_open(document):
            _deferred_undo_mode_restores.pop(key, None)
            continue
        if (
            document.getBookedTransactionID() != 0
            or document.HasPendingTransaction
        ):
            continue
        document.UndoMode = previous_mode
        _deferred_undo_mode_restores.pop(key, None)


def _retry_retained_transaction_closes() -> None:
    """Retry each retained close once at a later safe GUI event boundary."""

    global _transaction_retry_queued
    _transaction_retry_queued = False
    for transaction in tuple(_retained_transaction_closes.values()):
        try:
            transaction._retry_close()
        except RuntimeError:
            # Ownership remains retained. A later application transaction
            # boundary will schedule another single retry.
            pass
    _restore_ready_undo_modes()


def _queue_retained_transaction_retry() -> None:
    global _transaction_retry_queued
    if (
        _transaction_retry_queued
        or not _retained_transaction_closes
        or QtCore is None
    ):
        return
    _transaction_retry_queued = True
    QtCore.QTimer.singleShot(0, _retry_retained_transaction_closes)


class _NativeTransactionObserver:
    def slotCloseTransaction(self, _aborted: bool) -> None:
        # The application-wide close signal follows every participating
        # document's stable signal. It is therefore safe to restore UndoMode
        # here, but only when no successor transaction is present.
        _restore_ready_undo_modes()
        _queue_retained_transaction_retry()

    def slotDeletedDocument(self, document: Any) -> None:
        key = _document_key(document)
        _deferred_undo_mode_restores.pop(key, None)
        for retained_key in tuple(_retained_transaction_closes):
            if retained_key[0] == key:
                _retained_transaction_closes.pop(retained_key, None)


_native_transaction_observer = _NativeTransactionObserver()
App.addDocumentObserver(_native_transaction_observer)


class _OwnedDocumentTransaction:
    """Own and close exactly one document transaction.

    Native GUI commands use this only for finite, immediate mutations. Task
    panels remain owned by the native task-dialog transaction machinery.
    """

    def __init__(self, document: Any, name: str):
        self.document = document
        self.previous_undo_mode = int(document.UndoMode)
        self.temporary_undo_mode = self.previous_undo_mode == 0
        self.transaction_id = 0
        self._requested_abort: bool | None = None

        if (
            document.getBookedTransactionID() != 0
            or document.HasPendingTransaction
        ):
            raise RuntimeError("A document transaction is already active")

        if self.temporary_undo_mode:
            document.UndoMode = 1

        try:
            # The caller supplied the target document. Opening through the
            # application-wide active-document shortcut can silently book the
            # transaction on another document after a tab switch.
            # Document.openTransaction() deliberately returns None through the
            # Python binding. Read the ID back from the exact document instead
            # of assuming the binding forwards the C++ return value.
            document.openTransaction(name)
            self.transaction_id = int(document.getBookedTransactionID())
        except Exception:
            if self.temporary_undo_mode:
                document.UndoMode = self.previous_undo_mode
            raise

        if (
            self.transaction_id == 0
            or document.getBookedTransactionID() != self.transaction_id
        ):
            if (
                self.temporary_undo_mode
                and document.getBookedTransactionID() == 0
                and not document.HasPendingTransaction
            ):
                document.UndoMode = self.previous_undo_mode
            raise RuntimeError("Could not open the document transaction")

    def commit(self) -> None:
        self._close(abort=False, queue_retry=True)

    def abort(self) -> None:
        self._close(abort=True, queue_retry=True)

    def document_deleted(self) -> None:
        """Forget ownership after the exact document has been closed."""

        self.transaction_id = 0
        self._requested_abort = None

    def _retry_close(self) -> None:
        if self._requested_abort is not None:
            self._close(
                abort=self._requested_abort,
                queue_retry=False,
            )

    def _close(self, *, abort: bool, queue_retry: bool) -> None:
        if self.transaction_id == 0:
            return

        if (
            self._requested_abort is not None
            and self._requested_abort != abort
        ):
            original = "abort" if self._requested_abort else "commit"
            requested = "abort" if abort else "commit"
            raise RuntimeError(
                "Document transaction outcome is already retained as "
                f"{original}; refusing {requested}"
            )
        self._requested_abort = abort
        key = _document_key(self.document)
        retained_key = (key, self.transaction_id)
        if self.temporary_undo_mode:
            _deferred_undo_mode_restores[key] = (
                self.document,
                self.previous_undo_mode,
            )

        booked = int(self.document.getBookedTransactionID())
        if booked == self.transaction_id:
            App.closeActiveTransaction(abort, self.transaction_id)
            booked = int(self.document.getBookedTransactionID())

        if booked == self.transaction_id:
            _retained_transaction_closes[retained_key] = self
            if queue_retry:
                _queue_retained_transaction_retry()
            action = "abort" if abort else "commit"
            raise RuntimeError(
                f"Could not {action} document transaction "
                f"{self.transaction_id}"
            )

        # The exact transaction is gone. A stable callback may already have
        # opened a successor; never close or rename that successor.
        _retained_transaction_closes.pop(retained_key, None)
        self.transaction_id = 0
        self._requested_abort = None
        _restore_ready_undo_modes()
