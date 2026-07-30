# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact document identity and transaction ownership for Draft GUI actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

import FreeCAD as App

if App.GuiUp:
    import FreeCADGui as Gui
    from PySide import QtCore
else:
    Gui = None
    QtCore = None


@dataclass(frozen=True)
class DocumentReference:
    """Stable identity for one live document."""

    name: str
    uid: str

    @classmethod
    def capture(cls, document: Any) -> "DocumentReference":
        if document is None:
            raise ValueError("A Draft action requires a document")
        name = str(getattr(document, "Name", "") or "")
        uid = str(getattr(document, "Uid", "") or "")
        if not name or not uid or App.getDocument(name) is not document:
            raise ValueError("The Draft action document is not live")
        return cls(name, uid)

    def resolve(self) -> Any | None:
        try:
            document = App.getDocument(self.name)
        except (NameError, ReferenceError, RuntimeError):
            return None
        return (
            document
            if document is not None
            and str(getattr(document, "Uid", "") or "") == self.uid
            else None
        )


def object_is_usable_at_current_position(
    obj: Any,
    document: Any | None = None,
) -> bool:
    """Return whether *obj* is an exact, usable input at History's marker."""

    if obj is None:
        return False
    if document is None:
        document = getattr(obj, "Document", None)
    try:
        name = str(getattr(obj, "Name", "") or "")
        object_id = int(getattr(obj, "ID", -1))
        return bool(
            document is not None
            and getattr(obj, "Document", None) is document
            and App.getDocument(document.Name) is document
            and name
            and object_id >= 0
            and document.getObject(object_id) is obj
            and document.getObject(name) is obj
            and document.isObjectUsableAtCurrentTimelinePosition(obj)
        )
    except (AttributeError, NameError, ReferenceError, RuntimeError):
        return False


@dataclass(frozen=True)
class ObjectReference:
    """Stable identity for one object in a captured document."""

    document: DocumentReference
    name: str
    object_id: int

    @classmethod
    def capture(cls, obj: Any) -> "ObjectReference":
        document = getattr(obj, "Document", None)
        name = str(getattr(obj, "Name", "") or "")
        object_id = int(getattr(obj, "ID", -1))
        reference = DocumentReference.capture(document)
        if (
            not object_is_usable_at_current_position(obj, document)
        ):
            raise ValueError(
                "The Draft action object is not usable at the current "
                "History position"
            )
        return cls(reference, name, object_id)

    def resolve(self) -> Any | None:
        document = self.document.resolve()
        if document is None:
            return None
        try:
            obj = document.getObject(self.object_id)
        except (NameError, ReferenceError, RuntimeError):
            return None
        if (
            obj is None
            or str(getattr(obj, "Name", "") or "") != self.name
            or document.getObject(self.name) is not obj
            or not object_is_usable_at_current_position(obj, document)
        ):
            return None
        return obj


_deferred_undo_mode_restores: dict[
    DocumentReference,
    tuple[Any, int],
] = {}
_retained_transaction_closes: dict[
    tuple[DocumentReference, int],
    "OwnedDocumentTransaction",
] = {}
_transaction_retry_queued = False


def _document_has_pending_transaction(document: Any) -> bool:
    return bool(
        getattr(
            document,
            "HasPendingTransaction",
            False,
        )
    )


def document_is_available_for_mutation(document: Any) -> bool:
    """Return whether a new Draft action may own a transaction."""

    try:
        return bool(
            document is not None
            and App.getDocument(document.Name) is document
            and int(document.getBookedTransactionID()) == 0
            and not _document_has_pending_transaction(document)
            and App.getActiveTransaction() is None
        )
    except (NameError, ReferenceError, RuntimeError):
        return False


def selection_is_usable_for_document(document: Any) -> bool:
    """Return whether the complete GUI selection is usable by *document*."""

    if Gui is None or document is None:
        return False
    try:
        selection = tuple(Gui.Selection.getSelection())
    except (AttributeError, ReferenceError, RuntimeError):
        return False
    return bool(selection) and all(
        object_is_usable_at_current_position(obj, document)
        for obj in selection
    )


def gui_document_for(document: Any) -> Any | None:
    """Return the GUI peer for one exact live App document."""

    if Gui is None:
        return None
    try:
        reference = (
            document
            if isinstance(document, DocumentReference)
            else DocumentReference.capture(document)
        )
    except (AttributeError, NameError, ReferenceError, RuntimeError, ValueError):
        return None
    live_document = reference.resolve()
    if live_document is None:
        return None
    try:
        gui_document = Gui.getDocument(reference.name)
    except (NameError, ReferenceError, RuntimeError):
        return None
    return (
        gui_document
        if gui_document is not None
        and gui_document.Document is live_document
        else None
    )


def close_task_dialog(document: Any) -> None:
    """Close only the task dialog attached to one exact App document."""

    gui_document = gui_document_for(document)
    if gui_document is not None:
        Gui.Control.closeDialog(gui_document)


def reset_document_edit(document: Any) -> None:
    """Reset edit mode only in one exact live App document."""

    gui_document = gui_document_for(document)
    if gui_document is not None:
        gui_document.resetEdit()


def _restore_ready_undo_modes() -> None:
    for reference, (document, previous_mode) in tuple(
        _deferred_undo_mode_restores.items()
    ):
        live = reference.resolve()
        if live is None:
            _deferred_undo_mode_restores.pop(reference, None)
            continue
        if live is not document:
            _deferred_undo_mode_restores.pop(reference, None)
            continue
        if (
            int(document.getBookedTransactionID()) != 0
            or _document_has_pending_transaction(document)
        ):
            continue
        document.UndoMode = previous_mode
        _deferred_undo_mode_restores.pop(reference, None)


def _retry_retained_transaction_closes() -> None:
    global _transaction_retry_queued
    _transaction_retry_queued = False
    for transaction in tuple(_retained_transaction_closes.values()):
        try:
            transaction._retry_close()
        except RuntimeError:
            # A later stable/transaction boundary will schedule another retry.
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


class _TransactionObserver:
    def slotCloseTransaction(self, _aborted: bool) -> None:
        _restore_ready_undo_modes()
        _queue_retained_transaction_retry()

    def slotDeletedDocument(self, document: Any) -> None:
        name = str(getattr(document, "Name", "") or "")
        for reference in tuple(_deferred_undo_mode_restores):
            if reference.name == name:
                _deferred_undo_mode_restores.pop(reference, None)
        for key in tuple(_retained_transaction_closes):
            if key[0].name == name:
                transaction = _retained_transaction_closes.pop(key)
                transaction.document_deleted()


_transaction_observer = _TransactionObserver()
App.addDocumentObserver(_transaction_observer)


class OwnedDocumentTransaction:
    """Own and close exactly one Draft document transaction."""

    def __init__(self, document: Any, name: str):
        if not document_is_available_for_mutation(document):
            raise RuntimeError(
                "A Draft action cannot replace or join another transaction"
            )

        self.reference = DocumentReference.capture(document)
        self.previous_undo_mode = int(document.UndoMode)
        self.temporary_undo_mode = self.previous_undo_mode == 0
        self.transaction_id = 0
        self._requested_abort: bool | None = None

        if self.temporary_undo_mode:
            document.UndoMode = 1

        try:
            document.openTransaction(str(name))
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
                and int(document.getBookedTransactionID()) == 0
                and not _document_has_pending_transaction(document)
            ):
                document.UndoMode = self.previous_undo_mode
            raise RuntimeError("Could not establish the Draft transaction")

    @property
    def document(self) -> Any | None:
        return self.reference.resolve()

    @property
    def is_closed(self) -> bool:
        return self.transaction_id == 0

    def owns_current_transaction(self) -> bool:
        document = self.document
        return bool(
            document is not None
            and self.transaction_id != 0
            and int(document.getBookedTransactionID())
            == self.transaction_id
        )

    def commit(self) -> None:
        self._close(abort=False, queue_retry=True)

    def abort(self) -> None:
        self._close(abort=True, queue_retry=True)

    def document_deleted(self) -> None:
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
                "Draft transaction outcome is already retained as "
                f"{original}; refusing {requested}"
            )
        self._requested_abort = abort
        retained_key = (self.reference, self.transaction_id)
        document = self.document
        if document is None:
            _retained_transaction_closes.pop(retained_key, None)
            self.transaction_id = 0
            self._requested_abort = None
            return

        if self.temporary_undo_mode:
            _deferred_undo_mode_restores[self.reference] = (
                document,
                self.previous_undo_mode,
            )

        booked = int(document.getBookedTransactionID())
        if booked == self.transaction_id:
            App.closeActiveTransaction(abort, self.transaction_id)
            booked = int(document.getBookedTransactionID())

        if booked == self.transaction_id:
            _retained_transaction_closes[retained_key] = self
            if queue_retry:
                _queue_retained_transaction_retry()
            action = "abort" if abort else "commit"
            raise RuntimeError(
                f"Could not {action} Draft transaction "
                f"{self.transaction_id}"
            )

        # A synchronous close observer may have opened a successor. Never
        # close or rename it.
        _retained_transaction_closes.pop(retained_key, None)
        self.transaction_id = 0
        self._requested_abort = None
        _restore_ready_undo_modes()


def validate_object_references(
    document: Any,
    references: Iterable[ObjectReference],
) -> tuple[Any, ...]:
    """Resolve exact action inputs and reject deleted/replaced objects."""

    expected_document = DocumentReference.capture(document)
    resolved = []
    for reference in references:
        if reference.document != expected_document:
            raise RuntimeError(
                "A Draft action input belongs to another document"
            )
        obj = reference.resolve()
        if obj is None:
            raise RuntimeError(
                "A Draft action input was deleted, replaced, suppressed, or "
                "moved beyond the current History position before execution"
            )
        resolved.append(obj)
    return tuple(resolved)


def run_document_mutation(
    document: Any,
    name: str,
    callback: Callable[[], Any],
    *,
    objects: Iterable[Any] = (),
) -> Any:
    """Run one immediate Draft mutation as one exact undoable action."""

    document_reference = DocumentReference.capture(document)
    object_references = tuple(ObjectReference.capture(obj) for obj in objects)
    live_document = document_reference.resolve()
    if live_document is None:
        raise RuntimeError("The Draft action document was closed or replaced")
    validate_object_references(live_document, object_references)

    previous_document = App.activeDocument()
    previous_reference = (
        DocumentReference.capture(previous_document)
        if previous_document is not None
        else None
    )
    if App.activeDocument() is not live_document:
        App.setActiveDocument(live_document.Name)
    if App.activeDocument() is not live_document:
        raise RuntimeError("Could not activate the exact Draft action document")

    transaction = None
    try:
        transaction = OwnedDocumentTransaction(live_document, name)
        result = callback()
        if (
            document_reference.resolve() is not live_document
            or App.activeDocument() is not live_document
        ):
            raise RuntimeError(
                "The exact Draft action document changed during execution"
            )
        live_document.recompute()
    except Exception:
        if transaction is not None and not transaction.is_closed:
            transaction.abort()
        raise
    else:
        # A failed exact commit remains a retained commit request. Never turn
        # it into a rollback merely because closing was temporarily
        # unavailable.
        transaction.commit()
        return result
    finally:
        previous_live = (
            previous_reference.resolve()
            if previous_reference is not None
            else None
        )
        if (
            previous_live is not None
            and App.activeDocument() is not previous_live
        ):
            App.setActiveDocument(previous_reference.name)


def start_object_edit(obj: Any, mode: int = 0) -> bool:
    """Start the real editor on the object's exact GUI document."""

    if Gui is None:
        return False
    reference = ObjectReference.capture(obj)
    live_object = reference.resolve()
    document = reference.document.resolve()
    if (
        live_object is None
        or document is None
        or not document_is_available_for_mutation(document)
    ):
        return False
    gui_document = Gui.getDocument(document.Name)
    return bool(
        gui_document is not None
        and gui_document.Document is document
        and gui_document.setEdit(live_object, mode)
    )
