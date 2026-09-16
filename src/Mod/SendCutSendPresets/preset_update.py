# SPDX-License-Identifier: MIT
"""Use VibeCAD's native undo and cooperative mutation boundaries for presets."""


def run_document_update(document, operation, name="Apply bend preset"):
    """Run immediate edits and queue one recompute, preserving a task's undo scope."""
    if any(getattr(document, flag, False) for flag in
           ("Recomputing", "RecomputePending", "CooperativeMutationActive")):
        raise RuntimeError("Wait for the current document update to finish before applying a preset.")
    transaction = None
    legacy_owned = False
    booked = getattr(document, "getBookedTransactionID", lambda: 0)()
    if not booked and not getattr(document, "HasPendingTransaction", False):
        try:
            from VibeCADNativeTransaction import _OwnedDocumentTransaction
        except ImportError:
            document.openTransaction(name)
            legacy_owned = True
        else:
            transaction = _OwnedDocumentTransaction(document, name)
    begin = getattr(document, "beginCooperativeMutation", None)
    end = getattr(document, "endCooperativeMutation", None)
    mutation_started = False
    try:
        try:
            if callable(begin) and callable(end):
                begin()
                mutation_started = True
            result = operation()
            # Async recompute acquires its own mutation lease. Release the
            # immediate-edit lease before requesting that handoff.
            if mutation_started:
                end()
                mutation_started = False
            recompute = getattr(document, "recomputeAsync", None)
            if callable(recompute):
                recompute()
            else:
                document.recompute()
        except Exception:
            if transaction is not None:
                transaction.abort()
            elif legacy_owned:
                document.abortTransaction()
            raise
        if transaction is not None:
            transaction.commit()
        elif legacy_owned:
            document.commitTransaction()
        return result
    finally:
        if mutation_started:
            end()
