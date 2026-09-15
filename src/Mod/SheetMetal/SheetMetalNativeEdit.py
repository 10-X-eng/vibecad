# SPDX-License-Identifier: LGPL-2.1-or-later
"""Finalize native sheet edits only after their own asynchronous geometry settles."""

from concurrent.futures import Future
from contextlib import nullcontext

import FreeCAD as App

import SheetMetalHistoryOperations as Shared
from SheetMetalPresentation import _gui_thread
from VibeCADNativeMutation import NativeMutationRunner
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, is_structural_property


class NativeSheetEditError(RuntimeError):
    def __init__(self, message, *, object_name=None, parameters_committed=False):
        super().__init__(message)
        self.object_name = object_name
        self.parameters_committed = parameters_committed

    def failure(self):
        repair = "Inspect the current sheet and History before retrying. Committed parameters remain editable and can be undone through document History."
        if self.parameters_committed:
            repair += (
                " Creating another feature does not replace the retained one. "
                "If discarding it, inspect its dependencies and switch to Modeling; "
                "model.history operation=delete_features is available there for the exact object_name."
            )
        return {"error_code": "NATIVE_SHEETMETAL_EDIT_INCOMPLETE", "message": str(self),
                "object_name": self.object_name, "parameters_committed": self.parameters_committed,
                "repair": repair}


def _queue_tracked(document):
    return document.recomputeAsyncTracked()


def _history(document):
    return int(document.UndoCount), tuple(document.UndoNames)


class _Completion:
    def __init__(self, context, ticket):
        self.context, self.ticket = context, ticket
        self.document = context.document
        self.future = Future()
        self.execution = None
        self.run = None
        self.observing = False
        self.origins = set()
        self.origin = None
        self.invalid = None

    def transaction(self, **kwargs):
        self.transaction_name = kwargs["transaction_name"]
        self.checkpoint = self.context.undo_ledger.checkpoint(self.document)
        self.execution = NativeMutationRunner(self.context.state).start_deferred(
            ticket=self.ticket, reauthorize_turn=self.context.guard, **kwargs)
        self.history_after = _history(self.document)
        return self.execution.result

    def queue(self, document):
        if document is not self.document or self.execution is None:
            raise RuntimeError("The sheet edit lost its exact transaction owner")
        App.addDocumentObserver(self)
        self.observing = True
        request = _queue_tracked(document)
        if (not isinstance(request, dict) or request.get("request_count") != 1
                or not isinstance(request.get("origin"), str) or not request["origin"]):
            raise RuntimeError("The sheet edit did not queue one tracked document recompute")
        self.origin = request["origin"]

    def slotChangedObjectWithOrigin(self, obj, property_name, origin):
        if obj.Document is self.document and is_structural_property(property_name):
            # The origin arrives explicitly from the worker. Never infer it
            # from an open editor, the GUI thread, or a global pending flag.
            self.origins.add(origin)

    def slotCreatedObject(self, obj):
        if obj.Document is self.document:
            self.invalid = "Objects changed while the sheet geometry was preparing"

    def slotDeletedObject(self, obj):
        self.slotCreatedObject(obj)

    def slotAppendDynamicProperty(self, obj, _name):
        if obj is self.document or getattr(obj, "Document", None) is self.document:
            self.invalid = "The document property definitions changed during the sheet edit"

    def slotRemoveDynamicProperty(self, obj, name):
        self.slotAppendDynamicProperty(obj, name)

    def slotOpenTransaction(self, document, _name):
        if document is self.document:
            self.invalid = "Another document transaction started during the sheet edit"

    def slotUndoDocument(self, document):
        if document is self.document:
            self.invalid = "Document History moved during the sheet edit"

    def slotRedoDocument(self, document):
        self.slotUndoDocument(document)

    def slotDeletedDocument(self, document):
        if document is self.document:
            self.invalid = "The exact sheet document was closed"

    def close(self):
        if self.observing:
            self.observing = False
            App.removeDocumentObserver(self)

    def _verify_completion(self, result):
        self.context.guard()
        if App.getDocument(self.document.Name) is not self.document:
            raise RuntimeError("The exact sheet document was closed")
        if result["phase"] != "ready":
            raise RuntimeError(result.get("error") or "The sheet geometry is not ready")
        if self.invalid:
            raise RuntimeError(self.invalid)
        if self.origin is None or self.origins - {self.origin}:
            raise RuntimeError("Unrelated document edits occurred while sheet geometry was preparing")
        current = self.context.state.current_revision(self.context.document_uid)
        # One tracked request flushes one coalesced structural batch. A clean
        # no-op may produce no property changes and no additional revision.
        expected = self.execution.commit_revision + bool(self.origins)
        if current != expected or result["revision"]["structural_revision"] != current:
            raise RuntimeError("The document revision contains changes outside this sheet edit")
        if _history(self.document) != self.history_after:
            raise RuntimeError("The document Undo history changed during the sheet edit")

    def finish(self, completed):
        _gui_thread()
        result = None
        error = None
        try:
            # Cancellation may come from the provider thread. Atomically take
            # completion ownership before freezing a receipt, so it cannot be
            # cancelled between the last guard and result publication.
            if not self.future.set_running_or_notify_cancel():
                return
            result = dict(completed.result())
            self._verify_completion(result)
            state = self.context.state
            evidence = self.execution.prepared
            # Freeze final verified geometry evidence once, never rewrite a
            # completed receipt or shift an existing Undo guard to a new edit.
            prepared = state.prepare_mutation_completion(self.ticket, result,
                created=evidence.created, changed=evidence.changed,
                deleted=evidence.deleted, replaced=evidence.replaced)
            receipt = state.complete_prepared_mutation(prepared)
            scope = (self.context.undo_ledger.run_scope(self.context.run_id)
                     if self.context.run_id is not None else nullcontext())
            with scope:
                undo = self.execution.committed_undo_entry and self.context.undo_ledger.record_commit(
                    self.document, self.transaction_name, self.checkpoint, receipt)
            result.update(receipt=receipt.summary(), assistant_undo_available=bool(undo))
        except Exception as cause:
            error = NativeSheetEditError(str(cause),
                object_name=self.execution.result.get("object_name") if self.execution else None,
                parameters_committed=self.execution is not None)
            error.__cause__ = cause
        finally:
            self.close()
            self.context.state.cancel_mutation(self.ticket)
        # Detach observation before publishing: a Future callback can start
        # another legitimate document operation immediately.
        if error is not None:
            self.future.set_exception(error)
        else:
            self.future.set_result(result)


def start(context, ticket, prepared):
    """Return a Future for a shared History edit and its final owned receipt.

    The host calls this on the document thread. The native recompute owns the
    geometry workers; no document transaction or global mutation observer spans
    the asynchronous wait. Failed completion retains committed parameters.
    """
    _gui_thread()
    if not isinstance(context, NativeRuntimeContext) or not isinstance(ticket, NativeCallTicket):
        raise TypeError("Use an exact Native context and call ticket")
    context.guard()
    if (not isinstance(prepared, Shared.PreparedEdit)
            or prepared._sheet.Document is not context.document
            or ticket.document_uid != context.document_uid):
        raise RuntimeError("The prepared sheet edit belongs to another document")
    return _start_completion(context, ticket, lambda transaction, queue: Shared.start(
        prepared, transaction_runner=transaction, recompute_queue=queue))


def start_creation(context, ticket, source, reference_face, *, expected_revision):
    """Prepare shared folded/flat state from an exact existing source face."""
    import SheetMetalOperations as Operations
    _gui_thread()
    if not isinstance(context, NativeRuntimeContext) or not isinstance(ticket, NativeCallTicket):
        raise TypeError("Use an exact Native context and call ticket")
    context.guard()
    if source.Document is not context.document or ticket.document_uid != context.document_uid:
        raise RuntimeError("The sheet source belongs to another document")
    return _start_completion(context, ticket, lambda transaction, queue: Operations.start_creation(
        source, reference_face, expected_revision=expected_revision,
        transaction_runner=transaction, recompute_queue=queue))


def _start_completion(context, ticket, starter):
    receipt = context.state.completed_mutation_receipt(ticket)
    if receipt is not None:
        future = Future()
        future.set_result(dict(context.state.authorize_mutation(ticket).prior_verified_result))
        return future
    completion = _Completion(context, ticket)
    try:
        completion.run = starter(completion.transaction, completion.queue)
    except Exception:
        completion.close()
        context.state.cancel_mutation(ticket)
        raise
    completion.run.future.add_done_callback(completion.finish)
    return completion.future


def start_source_creation(context, ticket, prepared):
    """Validate an upstream source on its worker before recording native success."""
    import SheetMetalSourceOperations as Sources
    _gui_thread()
    if not isinstance(context, NativeRuntimeContext) or not isinstance(ticket, NativeCallTicket):
        raise TypeError("Use an exact Native context and call ticket")
    context.guard()
    if (not isinstance(prepared, Sources.PreparedSource) or prepared.document is not context.document
            or ticket.document_uid != context.document_uid):
        raise RuntimeError("The source request belongs to another document")
    return _start_completion(context, ticket, lambda transaction, queue: Sources.start(
        prepared, transaction_runner=transaction, recompute_queue=queue))
